"""GET /agents/scorecard + `scorekeeper.scorecard_for_api` (voce I-3 passo 1,
26/07 sera-5, Opus 5).

Il track record del comitato (74 call, hit-rate 52,7%) esisteva da settimane e
viveva SOLO dentro i prompt: `grep scorekeeper` su bellomberg_api.py e su
app/src dava 0. Questi test inchiodano le due proprieta' che rendono l'apertura
al PM onesta invece che pericolosa:

  1. l'endpoint NON RICALCOLA MAI (il calcolo apre 32 serie prezzi e dall'ambiente
     sbagliato ha gia' avvelenato la misura due volte: 25/07 sandbox audit,
     26/07 suite di test);
  2. ogni numero esce con l'IC 95% e con lo STATO dichiarato — su n=74 un
     hit-rate nudo del 52,7% e' indistinguibile da una moneta e la pagina non
     deve poter fingere il contrario.

Zero rete, zero DB: si scrivono snapshot finti su tmp_path.
"""
import ast
import json
import os

import pytest
from bellomberg.core.language import language_context

import bellomberg.agents.scorekeeper as sk

SANO = {
    "computed_at": "2026-07-26T10:00:00",
    "window_days": 270,
    "overall": {"n": 74, "hits": 39, "hit_rate_pct": 52.7,
                "avg_edge_pct": 1.54, "small_sample": False},
    "by_action": {"BUY": {"n": 21, "hits": 8, "hit_rate_pct": 38.1,
                          "avg_edge_pct": -1.57, "small_sample": False}},
    "by_confidence": {"ALTA": {"n": 9, "hits": 4, "hit_rate_pct": 44.4,
                               "avg_edge_pct": -0.54, "small_sample": True}},
    "by_specialist": {"quant": {"n": 30, "hits": 16, "hit_rate_pct": 53.3,
                                "avg_edge_pct": 0.9, "small_sample": False}},
    "n_unmeasurable": 96, "n_directional_candidates": 74, "n_fetch_fail": 0,
    "fetch_fail_causes": {}, "degraded": False,
    "worst_calls": [], "best_calls": [],
    "details": [{"id": 1, "ticker": "ALFA", "edge_pct": -3.1, "hit": False}],
    "method_note": "nota di metodo",
}


def _scrivi(tmp_path, scorecard, nome="snap.json"):
    p = tmp_path / nome
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"computed_at": scorecard.get("computed_at"),
                   "scorecard": scorecard}, f)
    return str(p)


# --------------------------------------------------------------------------
# 1. IC 95%: Wilson, perche' Wald mente sui campioni di casa
# --------------------------------------------------------------------------

def test_ic95_sul_campione_vero():
    ci = sk.wilson_ci95(74, 39)
    assert ci["method"] == "Wilson"
    assert 41.0 <= ci["low_pct"] <= 42.0 and 63.0 <= ci["high_pct"] <= 64.0
    assert ci["low_pct"] < 50.0 < ci["high_pct"], (
        "l'IC su n=74 DEVE contenere 50%: se un giorno non lo contiene piu' "
        "e' una notizia, non un dettaglio")


def test_ic95_non_mente_su_due_osservazioni():
    """Il bucket BASSA e' n=2 con 0 hit. Wald darebbe '0% +- 0' (certezza
    assoluta da due dati); Wilson dice che non si sa niente."""
    ci = sk.wilson_ci95(2, 0)
    assert ci["low_pct"] == 0.0
    assert ci["high_pct"] > 50.0, f"IC troppo stretto su n=2: {ci}"


def test_ic95_assente_su_campione_vuoto():
    assert sk.wilson_ci95(0, 0) is None


def test_ic95_non_sfonda_il_100():
    """Valori ESATTI, non solo i confini: con `high <= 100 and low > 0` il test
    passava anche con Wald, che su 5/5 restituisce l'intervallo degenere
    [100, 100] (review 26/07 sera-5)."""
    ci = sk.wilson_ci95(5, 5)
    assert (ci["low_pct"], ci["high_pct"]) == (56.6, 100.0), ci
    assert ci["width_pct_points"] > 0, "intervallo degenere: e' Wald, non Wilson"


def test_ic95_valori_esatti_sul_campione_vero():
    """Inchioda le cifre: 41,5-63,7 e' Wilson, 41,1-64,1 sarebbe Wald."""
    assert sk.wilson_ci95(74, 39)["low_pct"] == 41.5
    assert sk.wilson_ci95(74, 39)["high_pct"] == 63.7


def test_ic95_rifiuta_il_dominio_invalido():
    """`hits` assente diventava 0 per un `or 0` e produceva un IC [0%, 4.9%]
    servito ACCANTO a "hit-rate 52,7%": due misure incompatibili, entrambe
    presentate come vere. E con hits>n, p(1-p)<0 -> `** 0.5` da' un COMPLEX."""
    assert sk.wilson_ci95(10, None) is None
    assert sk.wilson_ci95(10, 50) is None
    assert sk.wilson_ci95(10, -1) is None
    assert sk.wilson_ci95(None, 5) is None
    assert sk.wilson_ci95(10, 0) is not None, "0 hit LEGITTIMI restano coperti"


def test_ampiezza_coerente_con_gli_estremi():
    """Calcolata sugli estremi grezzi discordava da high-low di 0,1 pt nel
    25,5% delle coppie (n=1..300): "[9,5-90,5], ampiezza 81,1" in pagina sono
    due misure discordanti zitte."""
    for n in range(1, 60):
        for k in range(0, n + 1):
            ci = sk.wilson_ci95(n, k)
            assert ci["width_pct_points"] == round(ci["high_pct"] - ci["low_pct"], 1), (n, k, ci)


# --------------------------------------------------------------------------
# 2. tricotomia dichiarata: un buco non si traveste da vuoto legittimo
# --------------------------------------------------------------------------

def test_snapshot_assente_dichiarato(tmp_path):
    out = sk.scorecard_for_api(str(tmp_path / "non_esiste.json"))
    assert out["available"] is False and out["stato"] == "assente"
    assert out["error"] and "assente" in out["error"]


def test_snapshot_illeggibile_dichiarato(tmp_path):
    p = tmp_path / "rotto.json"
    p.write_text("{ questo non e' json", encoding="utf-8")
    out = sk.scorecard_for_api(str(p))
    assert out["stato"] == "illeggibile" and out["error"]


def test_snapshot_senza_scorecard_dichiarato(tmp_path):
    p = tmp_path / "vuoto.json"
    p.write_text('{"computed_at": "2026-07-26T10:00:00"}', encoding="utf-8")
    out = sk.scorecard_for_api(str(p))
    assert out["stato"] == "illeggibile" and out["error"]


def test_snapshot_degradato_non_passa_per_vuoto(tmp_path):
    deg = dict(SANO, overall={"n": 0}, degraded=True, n_fetch_fail=32,
               fetch_fail_causes={"SLB": "ImportError: boom"},
               by_action={}, by_confidence={}, by_specialist={})
    out = sk.scorecard_for_api(_scrivi(tmp_path, deg))
    assert out["stato"] == "degradato" and out["available"] is True
    assert "MISURA FALLITA" in out["caveats"]["misura_fallita"]
    assert out["fetch_fail_causes"], "le cause non arrivano alla pagina"


def test_vuoto_genuino_distinto_dal_degradato(tmp_path):
    vuoto = dict(SANO, overall={"n": 0}, degraded=False, n_directional_candidates=0,
                 by_action={}, by_confidence={}, by_specialist={})
    out = sk.scorecard_for_api(_scrivi(tmp_path, vuoto))
    assert out["stato"] == "vuoto"
    assert "misura_fallita" not in out["caveats"]


def test_snapshot_sano(tmp_path):
    out = sk.scorecard_for_api(_scrivi(tmp_path, SANO))
    assert out["stato"] == "ok" and out["available"] is True and out["error"] is None
    assert out["overall"]["ci95"]["method"] == "Wilson"
    assert out["by_action"]["BUY"]["ci95"] and out["by_confidence"]["ALTA"]["ci95"]
    assert out["by_specialist"]["quant"]["ci95"]
    assert out["caveats"]["per_specialista"].startswith("EURISTICO")
    assert out["trend"]["available"] is False and out["trend"]["reason"]


def test_le_chiavi_di_contratto_ci_sono_in_ogni_ramo(tmp_path):
    """Pattern (25): chiave SEMPRE presente, il buco e' dichiarato nel valore.

    **Questo test, nella sua prima versione, NON POTEVA FALLIRE** (review 26/07
    sera-5): controllava 6 nomi, tutti dentro il dict iniziale, mentre le altre
    22 chiavi nascevano solo nel ramo buono e sparivano nei 4 rami d'errore —
    cioe' esattamente dove il PM ha piu' bisogno di capire. Ora il riferimento
    e' il ramo SANO: qualunque chiave lui produca, devono produrla tutti.
    """
    attese = set(sk.scorecard_for_api(_scrivi(tmp_path, SANO)).keys())
    assert len(attese) >= 25, f"contratto sospettosamente magro: {len(attese)} chiavi"

    rotto = tmp_path / "rotto2.json"
    rotto.write_text("{ non json", encoding="utf-8")
    nullo = tmp_path / "nullo.json"
    nullo.write_text("null", encoding="utf-8")
    lista = tmp_path / "lista.json"
    lista.write_text("[1,2]", encoding="utf-8")
    senza_ov = _scrivi(tmp_path, {k: v for k, v in SANO.items() if k != "overall"},
                       "senza_ov.json")
    rami = [str(tmp_path / "manca.json"), str(rotto), str(nullo), str(lista),
            senza_ov, _scrivi(tmp_path, SANO)]
    for r in rami:
        out = sk.scorecard_for_api(r)
        mancanti = attese - set(out.keys())
        assert not mancanti, (
            f"ramo '{out.get('stato')}': {len(mancanti)} chiavi di contratto "
            f"spariscono ({sorted(mancanti)[:6]}...) — la pagina esploderebbe qui")


def test_overall_assente_non_passa_per_finestra_VUOTA(tmp_path):
    """Il caso peggiore trovato dalla review: uno snapshot rotto rendeva
    `stato: vuoto`, che per contratto significa "nessuna call misurabile nel
    periodo". La pagina avrebbe scritto una verita' inventata."""
    out = sk.scorecard_for_api(_scrivi(tmp_path, {k: v for k, v in SANO.items()
                                                 if k != "overall"}))
    assert out["stato"] == "illeggibile", out["stato"]
    assert out["error"] and "overall" in out["error"]


def test_tipi_corrotti_non_diventano_un_500(tmp_path):
    """Un 500 e' dichiarato solo per uno sviluppatore: la pagina ha uno stato
    `illeggibile` fatto per questo. 6 forme corrotte alzavano AttributeError o
    TypeError fuori dal try (review 26/07 sera-5)."""
    for nome, sc in (("ov_str.json", dict(SANO, overall="boh")),
                     ("ov_lista.json", dict(SANO, overall=[1])),
                     ("n_str.json", dict(SANO, overall={"n": "molte", "hits": 1}))):
        out = sk.scorecard_for_api(_scrivi(tmp_path, sc, nome))
        assert out["stato"] == "illeggibile", (nome, out["stato"])
        assert out["error"], nome


def test_hits_assente_dichiara_lIC_non_calcolabile(tmp_path):
    out = sk.scorecard_for_api(_scrivi(tmp_path, dict(
        SANO, overall={"n": 74, "hit_rate_pct": 52.7})))
    assert out["overall"]["ci95"] is None
    assert "ci95_error" in out["overall"], (
        "IC assente ma senza spiegazione: il buco va DICHIARATO, non lasciato null")


def test_degraded_con_n_positivo_dichiara_lincoerenza(tmp_path):
    """Il writer garantisce `degraded => n==0`, ma qui si legge un FILE che puo'
    venire da qualsiasi versione: se le due cose convivono, si dice."""
    out = sk.scorecard_for_api(_scrivi(tmp_path, dict(SANO, degraded=True)))
    assert out["incoerenza"] and "degraded=True" in out["incoerenza"]
    assert out["stato"] == "ok", "n>0: la misura esiste, non e' il caso 'degradato'"


def test_degrado_PARZIALE_dichiarato_anche_al_PM(tmp_path):
    """Il Capo lo legge da sempre ("di cui N per fetch KO"), il PM no: due
    canali della stessa casa che dicevano cose diverse sullo stesso dato."""
    out = sk.scorecard_for_api(_scrivi(tmp_path, dict(
        SANO, n_fetch_fail=32, fetch_fail_causes={"SLB": "boom"})))
    assert out["stato"] == "ok"
    assert "fetch_parziale" in out["caveats"], "misura parziale servita come completa"
    assert out["fetch_fail_causes_troncate"] == 31, (
        "5 cause su 32 mostrate come se fossero tutte")


def test_la_finestra_dichiara_la_maturazione(tmp_path):
    """`window_days: 270` da solo e' fuorviante: la query esclude le decisioni
    degli ultimi 7 giorni, quindi il PM cercherebbe la call di martedi'."""
    out = sk.scorecard_for_api(_scrivi(tmp_path, SANO))
    assert out["maturation_days"] == sk.MIN_AGE_DAYS
    assert "giorni fa" in out["caveats"]["orizzonte"]


def test_eta_e_scadenza_dichiarate(tmp_path):
    vecchio = dict(SANO, computed_at="2026-01-01T00:00:00")
    out = sk.scorecard_for_api(_scrivi(tmp_path, vecchio))
    assert out["stale"] is True and out["age_hours"] > sk.SNAP_TTL_H
    assert out["ttl_hours"] == sk.SNAP_TTL_H


def test_eta_IGNOTA_non_e_dichiarata_fresca(tmp_path):
    """La prima versione di questo test asseriva `stale is False` su un
    `computed_at` illeggibile, cioe' **cristallizzava un fallback silenzioso**
    (review 26/07 sera-5): in pagina `stale=False` significa una cosa sola,
    "dato fresco", mentre la verita' era "non lo so". Ora: `None` + causa."""
    out = sk.scorecard_for_api(_scrivi(tmp_path, dict(SANO, computed_at="ieri sera")))
    assert out["stato"] == "ok" and out["age_hours"] is None
    assert out["stale"] is None, "eta' ignota spacciata per freschezza"
    assert out["age_error"] and "ieri sera" in out["age_error"]


def test_computed_at_con_timezone_non_diventa_fresco_per_sempre(tmp_path):
    """Non e' teorico: `datetime.now()` naive meno un ISO con tz alza TypeError.
    Prima finiva in un `except: pass` e il payload diceva "fresco" in eterno."""
    out = sk.scorecard_for_api(_scrivi(tmp_path, dict(
        SANO, computed_at="2026-07-26T10:00:00+02:00")))
    assert out["stale"] is None and out["age_error"]


def test_computed_at_nel_futuro_dichiara_lorologio_sfasato(tmp_path):
    out = sk.scorecard_for_api(_scrivi(tmp_path, dict(SANO, computed_at="2099-01-01T00:00:00")))
    assert out["age_hours"] < 0 and "FUTURO" in (out["age_error"] or "")


def test_lo_snapshot_letto_non_viene_mutato(tmp_path):
    p = _scrivi(tmp_path, SANO)
    prima = open(p, encoding="utf-8").read()
    out = sk.scorecard_for_api(p)
    assert "ci95" in out["overall"]
    assert open(p, encoding="utf-8").read() == prima, "il file e' stato riscritto"
    assert "ci95" not in SANO["overall"], "mutato il dizionario di partenza"


# --------------------------------------------------------------------------
# 3. il vincolo che vale: da qui non si ricalcola MAI
# --------------------------------------------------------------------------

def test_leggere_il_payload_non_ricalcola(tmp_path, monkeypatch):
    """Snapshot deliberatamente VECCHIO (gennaio): con `SANO` (computed_at
    hardcodato a stamattina) il test era **flaky per calendario** — avrebbe
    preso un ricalcolo condizionato `if stale:` solo dopo le 22:00 di quel
    giorno (review 26/07 sera-5)."""
    def _boom(*a, **k):
        raise AssertionError("compute_scorecard chiamata dalla lettura per la UI")
    monkeypatch.setattr(sk, "compute_scorecard", _boom)
    vecchio = _scrivi(tmp_path, dict(SANO, computed_at="2026-01-01T00:00:00"))
    out = sk.scorecard_for_api(vecchio)
    assert out["stato"] == "ok" and out["stale"] is True
    assert out["ricalcolo_da_questo_endpoint"] is False


def _funzione(percorso_file, nome):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, percorso_file), encoding="utf-8").read()
    # AsyncFunctionDef inclusa: cercare solo FunctionDef dava un FALSO POSITIVO
    # ("endpoint scomparso") su un refactor legittimo a `async def`
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == nome), None)
    assert fn is not None, f"{nome} scomparsa da {percorso_file}"
    return fn


def _simboli(fn):
    """Nomi, kwargs e import del CORPO, docstring esclusa: il docstring cita
    `force=True` per spiegare di chi e' il ricalcolo, e un match sul testo
    abbaierebbe a lui."""
    corpo = [n for n in fn.body if not (isinstance(n, ast.Expr)
                                        and isinstance(n.value, ast.Constant)
                                        and isinstance(n.value.value, str))]
    nomi, kwargs = set(), set()
    for ramo in corpo:
        for n in ast.walk(ramo):
            if isinstance(n, ast.Name):
                nomi.add(n.id)
            elif isinstance(n, ast.Attribute):
                nomi.add(n.attr)
            elif isinstance(n, ast.keyword) and n.arg:
                kwargs.add(n.arg)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                nomi.add(getattr(n, "module", None) or "")
                nomi.update(a.name for a in n.names)
    return nomi, kwargs


def test_guardia_strutturale_endpoint_non_ricalcola():
    """Tripwire sul SORGENTE: se un domani qualcuno infila un ricalcolo 'per
    avere il dato fresco', questo test cade prima che l'API apra 32 serie prezzi
    a ogni refresh della pagina.

    **LISTA BIANCA, non lista nera** (review 26/07 sera-5: la lista nera era
    aggirabile in 5 modi su 9 — helper nel modulo, `get_track_record_for_capo`
    che ricalcola, query param `?refresh=1`, thread, ricalcolo dentro
    `scorecard_for_api`). Qui l'endpoint puo' nominare SOLO cio' che serve.
    """
    fn = _funzione("src/bellomberg/api/bellomberg_api.py", "get_agents_scorecard")
    nomi, kwargs = _simboli(fn)
    ammessi = {"bellomberg.agents.scorekeeper", "scorecard_for_api",
               "_err500", "_api_text", "e", "Exception"}
    intrusi = nomi - ammessi
    assert not intrusi, (
        f"nomi non ammessi nell'endpoint: {sorted(intrusi)}. Deve limitarsi a "
        "servire scorecard_for_api(): qualunque altra cosa va provata a mano "
        "e aggiunta qui consapevolmente.")
    assert "force" not in kwargs
    assert "scorecard_for_api" in nomi
    # nessun parametro: uccide il `?refresh=1` prima che venga in mente
    args = fn.args
    assert not (args.args or args.kwonlyargs or args.posonlyargs or
                args.vararg or args.kwarg), (
        "l'endpoint ha preso dei parametri: un `?refresh=` riaprirebbe il "
        "ricalcolo su HTTP dalla porta di servizio")


@pytest.mark.parametrize('language,hint', [('it', 'lettura snapshot track record'), ('en', 'Reading the track record snapshot')])
def test_endpoint_error_uses_real_language_helper_without_recalculation(monkeypatch, language, hint):
    from bellomberg.api import bellomberg_api as api
    from fastapi import HTTPException
    reads = []
    def failed_read():
        reads.append(True)
        raise OSError('Original snapshot diagnostic')
    def forbidden(*a, **kw):
        pytest.fail('Error handling must not recalculate the scorecard')
    monkeypatch.setattr(sk, 'scorecard_for_api', failed_read)
    monkeypatch.setattr(sk, 'compute_scorecard', forbidden)
    with language_context(language), pytest.raises(HTTPException) as caught:
        api.get_agents_scorecard()
    assert caught.value.status_code == 500
    assert caught.value.detail == hint + ' — OSError: Original snapshot diagnostic'
    assert len(reads) == 1


def test_anche_la_funzione_a_valle_non_ricalcola():
    """Il bypass PIU' PROBABILE non e' nell'endpoint: e' mettere
    `if stale: compute_scorecard(force=True)` dentro `scorecard_for_api`,
    dove la guardia dell'endpoint non guarda."""
    for nome in ("scorecard_for_api", "_arricchisci"):
        nomi, kwargs = _simboli(_funzione("src/bellomberg/agents/scorekeeper.py", nome))
        assert "compute_scorecard" not in nomi, (
            f"{nome} ricalcola: la lettura per la UI deve solo servire il file")
        assert "yfinance" not in nomi and "force" not in kwargs


def test_endpoint_registrato_una_volta_sola():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "src", "bellomberg", "api", "bellomberg_api.py"),
               encoding="utf-8").read()
    # conta la ROTTA, non una grafia esatta: con apici singoli o un kwarg in
    # mezzo il vecchio `count('@app.get("/agents/scorecard")')` dava 0
    rotte = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Constant) and n.value == "/agents/scorecard"]
    assert len(rotte) == 1, f"rotta dichiarata {len(rotte)} volte"
