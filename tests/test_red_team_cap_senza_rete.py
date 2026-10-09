"""V0-REDTEAM (05/10): il tetto del provider del Red Team (93a71a9) si leggeva con una GET propria
alla Models API a meta' run, oltre a quella del controllo prezzi del registro richieste: due letture
(che possono dare tetti diversi) e una chiamata di rete vera dentro il replay offline.
Ora il Red Team legge il listino dal registro richieste della run (la stessa memoria che la sonda
dei modelli e il controllo prezzi riempiono): una sola lettura per modello e per run.
MOD-CAP (06/10): il tetto ora si adatta nel punto UNICO del client (llm_client.tetto_uscita, letto
dal listino del registro richieste dentro una run): le stesse garanzie si provano li'.
Modelli e numeri inventati."""
import json
import sys

import pytest
import requests

from bellomberg.agents import red_team
from bellomberg.core import llm_client, current_facts as REAL_CURRENT_FACTS
from bellomberg.core.request_journal import RequestJournal, request_scope
from bellomberg.valuation import preparation_ai

MODELLO = "zz/finto-flash-9"


def _listino(model):
    return {"id": model, "context_length": 900_000, "top_provider": {"max_completion_tokens": 65536},
            "pricing": {"prompt": "0.000001", "completion": "0.000002"}}


@pytest.fixture(autouse=True)
def dichiarazioni_nuove(monkeypatch):
    # Le dichiarazioni del tetto sono UNA per (ruolo, modello) per processo: ogni test riparte.
    monkeypatch.setattr(llm_client, "_TETTI_DICHIARATI", set())
    monkeypatch.setattr(llm_client, "_LISTINO_PROCESSO", {})


@pytest.fixture
def niente_rete_diretta(monkeypatch):
    # Il Red Team non deve aggirare il registro con una sua GET.
    def vietata(model):
        pytest.fail("il Red Team ha letto il catalogo fuori dal registro richieste: " + str(model))
    monkeypatch.setattr(preparation_ai, "live_metadata", vietata)


def _registro(tmp_path, metadata):
    return RequestJournal(tmp_path / "zz-requests.sqlite", run_id="zz-run",
                          authorization={"scope": "test sintetico"}, metadata=metadata)


def test_listino_letto_dalla_sonda_riusato_senza_rete(tmp_path, capsys, niente_rete_diretta):
    letture = []
    registro = _registro(tmp_path, lambda m: letture.append(m) or _listino(m))
    registro._quote({"model": MODELLO, "max_tokens": 1000})       # la sonda dei modelli
    assert letture == [MODELLO]
    with request_scope(registro, phase="red_team", agent="_red_team", round_n=1):
        assert llm_client.tetto_uscita(MODELLO, 128000, ruolo="red_team") == 65536
    assert letture == [MODELLO]                                    # nessuna seconda lettura
    out = capsys.readouterr().out
    assert "richiesti 128000 token" in out and "uso 65536" in out and "[listino della run]" in out


def test_ripresa_senza_sonda_una_sola_lettura_condivisa_col_controllo_prezzi(tmp_path, capsys,
                                                                             niente_rete_diretta):
    # Ripresa oltre il priming: la sonda non gira, la memoria del registro e' vuota.
    letture = []
    registro = _registro(tmp_path, lambda m: letture.append(m) or _listino(m))
    with request_scope(registro, phase="red_team", agent="_red_team", round_n=1):
        cap = llm_client.tetto_uscita(MODELLO, 128000, ruolo="red_team")
    assert cap == 65536 and letture == [MODELLO]
    out = capsys.readouterr().out
    assert "[listino della run]" in out
    registro._quote({"model": MODELLO, "max_tokens": cap})         # il controllo prezzi del Red Team
    assert letture == [MODELLO]                                    # lo stesso listino, non riletto


def test_catalogo_irraggiungibile_dichiarato_senza_crash(tmp_path, capsys, niente_rete_diretta):
    def giu(model):
        raise requests.ConnectionError("rete giu' (sintetico)")
    registro = _registro(tmp_path, giu)
    with request_scope(registro, phase="red_team", agent="_red_team", round_n=1):
        assert llm_client.tetto_uscita(MODELLO, 128000, ruolo="red_team") == 128000
    out = capsys.readouterr().out
    assert "listino non letto (ConnectionError)" in out and "uso i 128000 richiesti" in out
    assert "rete giu'" not in out                                  # solo il tipo, mai il testo


def test_senza_registro_lettura_diretta_come_prima(monkeypatch):
    letture = []
    monkeypatch.setattr(preparation_ai, "live_metadata", lambda m: letture.append(m) or _listino(m))
    assert llm_client.tetto_uscita(MODELLO, 128000, ruolo="chat") == 65536
    assert llm_client.tetto_uscita(MODELLO, 128000, ruolo="chat") == 65536
    assert letture == [MODELLO]                    # fuori run: cache di processo (TTL monotonico)


from test_pipeline_replay_artifacts import replay, replay_loop, run_offline, db  # noqa: E402,F401


@pytest.fixture
def native_research_replay(replay, monkeypatch):
    # The archived replay replaces this whole module. New contracts need the real
    # note capture, frozen context and delivery receipts; only live context stays fake.
    import bellomberg.core as core_package
    archived_facts = sys.modules['bellomberg.core.current_facts']
    monkeypatch.setitem(sys.modules, 'bellomberg.core.current_facts', REAL_CURRENT_FACTS)
    monkeypatch.setattr(core_package, 'current_facts', REAL_CURRENT_FACTS)
    for name in ('current_facts_block', 'favorites_block', 'pm_theses_block'):
        monkeypatch.setattr(REAL_CURRENT_FACTS, name, getattr(archived_facts, name))
    return replay


def test_replay_completo_red_team_al_tetto_con_una_lettura(native_research_replay, run_offline, monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    replay = native_research_replay
    monkeypatch.setattr(cm, "_weekly_contract", run_offline.native_weekly_contract)
    result = cm.run_multi_agent(language="it")
    assert result['status'] == 'completed', result.get('last_error')
    assert replay.blackboard.weekly_store.get(REAL_CURRENT_FACTS.RESEARCH_NOTES_STAGE)['policy'] == REAL_CURRENT_FACTS.RESEARCH_NOTES_POLICY
    rossi = [c for c in replay.side_calls if "RISK MANAGER SCETTICO" in str(c.get("system"))]
    # MOD-CAP: il Red Team chiede il cap del contratto; il client vero lo adatta sul filo
    # (qui il client e' finto ad alto livello: l'adattamento si prova in test_tetto_uscita_adattivo).
    assert rossi and {c["max_tokens"] for c in rossi} == {red_team.WEEKLY_RED_MAX_TOKENS}
    modello = rossi[0]["model"]
    assert replay.catalog_reads.count(modello) == 1      # una sola lettura per run
    assert not replay.network


# ------------------------------------------------------------ preventivo prima dell'invio
# Decisione main/PM 05/10: un guasto del preventivo del Red Team (catalogo giu', cap oltre il
# tetto del provider) avviene PRIMA di qualsiasi invio: nessuna spesa, lacuna DICHIARATA, la run
# prosegue. Un errore di programmazione resta errore di run.

def _senza_preventivo():
    return getattr(red_team, "RedTeamSenzaPreventivo", None) or type("Assente", (Exception,), {})


def test_preventivo_cap_oltre_il_tetto_e_lacuna_per_tipo(tmp_path):
    registro = _registro(tmp_path, _listino)
    # MOD-CAP: il preventivo valida il cap che partira' sul filo (adattato al tetto); il rifiuto
    # per prezzo/contesto resta lacuna per tipo.
    with request_scope(registro, phase="red_team", agent="_red_team", round_n=1):
        assert red_team._preventivo_prima_dell_invio(MODELLO, 128000) == 65536
        assert red_team._preventivo_prima_dell_invio(MODELLO, 65536) == 65536
    stretto = lambda m: {**_listino(m), "context_length": 60000}
    with request_scope(_registro(tmp_path / "b", stretto), phase="red_team", agent="_red_team", round_n=1):
        with pytest.raises(_senza_preventivo(), match="context or completion cap"):
            red_team._preventivo_prima_dell_invio(MODELLO, 128000)


def test_preventivo_catalogo_giu_solo_il_tipo_nel_messaggio(tmp_path):
    def giu(model):
        raise requests.ConnectionError("https://example.invalid/?segreto=zz")
    with request_scope(_registro(tmp_path, giu), phase="red_team", agent="_red_team", round_n=1):
        with pytest.raises(_senza_preventivo()) as caduta:
            red_team._preventivo_prima_dell_invio(MODELLO, 65536)
    assert "ConnectionError" in str(caduta.value) and "segreto" not in str(caduta.value)
    assert "nessuna spesa" in str(caduta.value)


def test_preventivo_errore_di_programmazione_non_diventa_lacuna(tmp_path):
    def rotto(model):
        raise TypeError("synthetic programming error")
    with request_scope(_registro(tmp_path, rotto), phase="red_team", agent="_red_team", round_n=1):
        with pytest.raises(TypeError):
            red_team._preventivo_prima_dell_invio(MODELLO, 65536)


def test_allow_list_della_lacuna_per_tipo_non_per_testo():
    from bellomberg.agents import weekly_lifecycle as wl
    assert wl._red_team_local_failure(_senza_preventivo()("qualsiasi testo"))
    assert not wl._red_team_local_failure(ValueError("requested completion cap exceeds provider"))
    assert not wl._red_team_local_failure(TypeError("synthetic"))


def test_replay_catalogo_giu_al_red_team_run_completa_con_lacuna(native_research_replay, run_offline, monkeypatch):
    # Research mode (contratto nativo): il listino non e' leggibile, nessun invio del Red Team,
    # la run arriva in fondo con la lacuna dichiarata.
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _store
    replay = native_research_replay
    monkeypatch.setattr(cm, "_weekly_contract", run_offline.native_weekly_contract)

    def giu(model):
        replay.catalog_reads.append(model)
        raise requests.ConnectionError("synthetic catalog down")
    monkeypatch.setattr(preparation_ai, "live_metadata", giu)
    result = cm.run_multi_agent(language="it")
    assert result["status"] == "completed", result.get("last_error")
    assert _store().get(REAL_CURRENT_FACTS.RESEARCH_NOTES_STAGE)['policy'] == REAL_CURRENT_FACTS.RESEARCH_NOTES_POLICY
    assert not [c for c in replay.side_calls if "RISK MANAGER SCETTICO" in str(c.get("system"))]
    assert _store().get("red_team")["gap"].startswith("RedTeamSenzaPreventivo")
    assert not replay.network


_MEMO_CON_ALTA = ("# Memo settimanale\n\n## ACTION TABLE\n"
                  "| Action | Ticker | Size | Timing | Confidence |\n"
                  "|---|---|---|---|---|\n"
                  "| HOLD | ZZTEST | 0 EUR | ora | **ALTA** |\n\n"
                  "Tesi sintetica con convinzione ALTA dichiarata dal Capo. " + "m" * 80)


@pytest.fixture
def legacy_red_team(run_offline, monkeypatch):
    """Run NON research (contratto legacy di run_offline) col Capo che scrive ALTA."""
    from bellomberg.agents import consigliere_multi as cm
    capo = cm.run_capo

    def capo_alta(bb, **kwargs):
        capo(bb, **kwargs)
        return _MEMO_CON_ALTA, {"model": "m/finto", "input_tokens": 10, "output_tokens": 10,
                                "api_calls": 1, "complete": True, "stop_reason": "end_turn"}
    monkeypatch.setattr(cm, "run_capo", capo_alta)
    return cm


def test_fuori_research_red_team_senza_preventivo_prosegue_alta_declassata(legacy_red_team, run_offline,
                                                                         monkeypatch):
    cm = legacy_red_team
    from test_weekly_recovery import _store

    def red(bb, **kwargs):
        raise _senza_preventivo()("Red Team senza preventivo prima dell'invio (nessuna spesa): sintetico")
    monkeypatch.setattr(red_team, "run_red_team", red)
    result = cm.run_multi_agent(send_email=True)
    store = _store()
    assert store.context["contract"].get("analysis_mode") is None          # davvero non research
    assert result["status"] == "completed", result.get("last_error")
    assert store.get("red_team")["gap"].startswith("RedTeamSenzaPreventivo")
    assert "RED TEAM MANCANTE" in run_offline.catturato["sizing_context"]
    memo = store.get("memo_validated")["memo"]
    riga = [line for line in memo.splitlines() if line.startswith("| HOLD")]
    assert riga and "ALTA" not in riga[0] and "**MEDIA**" in riga[0]
    assert "ALTA declassata a MEDIA" in memo and "**Red Team.** Assente" in memo


def test_fuori_research_errore_di_programmazione_del_red_team_ferma_la_run(legacy_red_team, monkeypatch):
    cm = legacy_red_team
    from test_weekly_recovery import _store

    def red(bb, **kwargs):
        raise TypeError("synthetic programming error in the red team")
    monkeypatch.setattr(red_team, "run_red_team", red)
    with pytest.raises(TypeError, match="synthetic programming error"):
        cm.run_multi_agent(send_email=True)
    assert _store().get("red_team") is None


def test_fuori_research_red_team_presente_conserva_alta(legacy_red_team, run_offline):
    # Controllo: togliere la guardia research non deve declassare ALTA quando il Red Team c'e'.
    cm = legacy_red_team
    from test_weekly_recovery import _store
    result = cm.run_multi_agent(send_email=True)
    assert result["status"] == "completed", result.get("last_error")
    memo = _store().get("memo_validated")["memo"]
    riga = [line for line in memo.splitlines() if line.startswith("| HOLD")]
    assert riga and "**ALTA**" in riga[0] and "ALTA declassata" not in memo
    assert "RED TEAM MANCANTE" not in run_offline.catturato["sizing_context"]


def test_fuori_research_la_lacuna_vale_solo_per_il_red_team():
    # I desk fuori research restano errore di run (il quorum e' del contratto research).
    from types import SimpleNamespace
    from bellomberg.agents import weekly_lifecycle as wl
    from bellomberg.core.llm_client import APIConnectionError
    bb = SimpleNamespace(analysis_mode=None)
    registro = SimpleNamespace(summary=lambda: {"unknown_requests": 0})
    store = SimpleNamespace(context={"contract": {"roster": ["macro"]}}, request_journal=registro)
    assert wl.gap_kind(bb, store, APIConnectionError("synthetic"), "macro") is None
    assert wl.gap_kind(bb, store, _senza_preventivo()("synthetic"), "_red_team") == "red_team"
    assert wl.gap_kind(bb, store, TypeError("synthetic"), "_red_team") is None


# ------------------------------------------------------------ revisione R-0RT (06/10)
# Le prove del revisore asserivano il difetto: qui sono ribaltate sulla regola decisa da main/PM.

def test_RA_costo_incerto_non_trasforma_la_lacuna_in_errore_di_run():
    # Regola PM 05/10: un costo incerto si DICHIARA e non blocca (prevale sul 04/10).
    from types import SimpleNamespace
    from bellomberg.agents import weekly_lifecycle as wl
    from bellomberg.core.llm_client import APIConnectionError
    registro = SimpleNamespace(summary=lambda: {"unknown_requests": 1, "requests": []})
    store = SimpleNamespace(context={"contract": {"roster": ["macro"]}}, request_journal=registro)
    errore = _senza_preventivo()("sintetico, nessuna spesa")
    bb = SimpleNamespace(analysis_mode=None)
    assert wl.gap_kind(bb, store, errore, "_red_team") == "red_team"
    bb.analysis_mode = "fundamentals_research_v1"
    assert wl.gap_kind(bb, store, errore, "_red_team") == "red_team"
    assert wl.gap_kind(bb, store, APIConnectionError("synthetic"), "macro") == "desk"
    # registro non verificabile: idem, lacuna (l'incertezza si dichiara nel messaggio)
    assert wl.gap_kind(bb, SimpleNamespace(context=store.context, request_journal=None), errore, "_red_team") == "red_team"


@pytest.mark.parametrize("cap", [65536.0, "65536", True, 0, -5, None])
def test_RB_max_tokens_non_intero_e_errore_di_programmazione(tmp_path, cap):
    with request_scope(_registro(tmp_path, _listino), phase="red_team", agent="_red_team", round_n=1):
        with pytest.raises(TypeError):
            red_team._preventivo_prima_dell_invio(MODELLO, cap)


def test_RB_modello_configurato_assente_dal_catalogo_e_lacuna(tmp_path):
    def assente(model):
        raise ValueError("configured model absent or ambiguous in live pricing catalog")
    with request_scope(_registro(tmp_path, assente), phase="red_team", agent="_red_team", round_n=1):
        with pytest.raises(_senza_preventivo(), match="configured model absent"):
            red_team._preventivo_prima_dell_invio(MODELLO, 65536)


@pytest.mark.parametrize("campi", [
    {"pricing": {"prompt": "n/d", "completion": "0.1"}},          # InvalidOperation
    {"pricing": {"prompt": "NaN", "completion": "0.1"}},
    {"pricing": None},                                            # AttributeError
    {"pricing": "gratis"},
    {"top_provider": {"max_completion_tokens": "65536"}},
    {"top_provider": "x"},
    {"context_length": None},
])
def test_RC_listino_anomalo_e_lacuna_dichiarata(tmp_path, campi):
    with request_scope(_registro(tmp_path, lambda m: {**_listino(m), **campi}),
                       phase="red_team", agent="_red_team", round_n=1):
        with pytest.raises(_senza_preventivo(), match="listino|cap|context|pricing"):
            red_team._preventivo_prima_dell_invio(MODELLO, 1000)


def test_RC_listino_non_dizionario_e_lacuna(tmp_path):
    with request_scope(_registro(tmp_path, lambda m: ["non", "un", "listino"]),
                       phase="red_team", agent="_red_team", round_n=1):
        with pytest.raises(_senza_preventivo()):
            red_team._preventivo_prima_dell_invio(MODELLO, 1000)


@pytest.mark.parametrize("corpo", [{"errore": "sintetico"}, {"data": None}, {"data": ["riga non dict"]}, []])
def test_RC_catalogo_senza_data_e_valueerror(monkeypatch, corpo):
    class Risposta:
        def raise_for_status(self):
            pass

        def json(self):
            return corpo
    monkeypatch.setattr(requests, "get", lambda *a, **k: Risposta())
    with pytest.raises(ValueError, match="catalog"):
        preparation_ai.live_metadata(MODELLO)


def test_RD_flap_del_catalogo_il_cap_viene_dal_listino_del_preventivo(tmp_path, capsys):
    letture = {"n": 0}

    def flap(model):
        letture["n"] += 1
        if letture["n"] == 1:
            raise requests.ConnectionError("giu' (sintetico)")
        return _listino(model)
    with request_scope(_registro(tmp_path, flap), phase="red_team", agent="_red_team", round_n=1):
        cap = llm_client.tetto_uscita(MODELLO, 128000, ruolo="red_team")
        assert cap == 128000                                       # prima lettura giu': dichiarato
        assert red_team._preventivo_prima_dell_invio(MODELLO, cap) == 65536
    assert letture["n"] == 2
    assert "uso 65536" in capsys.readouterr().out


def test_RD_tetto_sopra_il_contesto_e_lacuna(tmp_path):
    # MOD-CAP: il contratto salvato non cambia (lo adatta il client); un listino il cui contesto
    # non contiene nemmeno il cap adattato resta rifiutato PRIMA dell'invio, lacuna per tipo.
    stretto = lambda m: {**_listino(m), "context_length": 1000}
    with request_scope(_registro(tmp_path, stretto), phase="red_team", agent="_red_team", round_n=1):
        with pytest.raises(_senza_preventivo(), match="cap unavailable"):
            red_team._preventivo_prima_dell_invio(MODELLO, 128000)


def test_RE_comitato_non_calcolato_non_dice_red_team_mancante():
    from bellomberg.agents import weekly_lifecycle as wl
    righe = [{"row_index": 0, "action": "HOLD", "ticker": "ZZTEST"}]
    blocco = wl.conviction_cap_block(righe, [], "it", comitato_non_calcolato=True)
    assert "non ha completato" not in blocco and "non calcolato" in blocco
    assert "ALTA declassata a MEDIA" in blocco
    assert "Il Red Team non ha completato" in wl.conviction_cap_block(righe, [], "it")


def test_RA_red_team_con_costo_incerto_lacuna_dichiarata_nessun_reinvio(legacy_red_team, monkeypatch):
    # Rete instabile: una richiesta resta a costo incerto, poi il Red Team non ha il preventivo.
    import httpx
    from bellomberg.core.llm_client import OpenRouterClient
    from test_weekly_recovery import _store
    cm = legacy_red_team
    monkeypatch.setattr(preparation_ai, "live_metadata", _listino)
    inviate = []

    def send(request):
        inviate.append(request)
        return httpx.Response(503, json={"error": {"code": 503, "message": "synthetic provider outage"}})
    client = OpenRouterClient(api_key="test", max_retries=4, trasporto=httpx.MockTransport(send))

    def red(bb, **kwargs):
        with pytest.raises(Exception):
            client.messages.create(model=MODELLO, max_tokens=20,
                                   messages=[{"role": "user", "content": "frozen weekly input"}])
        raise _senza_preventivo()("Red Team senza preventivo prima dell'invio (nessuna spesa): sintetico")
    monkeypatch.setattr(red_team, "run_red_team", red)
    # send_email=False: con un costo incerto la CONSEGNA email e' bloccata da un cancello a parte
    # (weekly_lifecycle._deliver_checked), fuori da questa voce: dichiarato nel rapporto.
    result = cm.run_multi_agent(send_email=False)
    store = _store()
    assert len(inviate) == 1                                      # nessun reinvio automatico
    assert result["request_costs"]["unknown_requests"] == 1       # il costo resta incerto, dichiarato
    gap = store.get("red_team")["gap"]
    assert gap.startswith("RedTeamSenzaPreventivo") and "costo incerto" in gap
    assert store.get("memo_validated") is not None                # la run arriva in fondo
    assert result["status"] == "incomplete" and result["analytical_status"] == "complete"


def test_RE_run_comitato_non_calcolato_avviso_vero_al_capo(legacy_red_team, run_offline, monkeypatch):
    from bellomberg.reporting import weekly_gaps
    from test_weekly_recovery import _store
    cm = legacy_red_team

    vero = weekly_gaps.committee_gaps_summary
    chiamate = []

    def guasto_prima_del_capo(*args, **kwargs):
        # Solo il riepilogo PRIMA del Capo fallisce. (Con il riepilogo finale non calcolato le
        # decisioni non sono ammesse e la run si ferma: cancello preesistente, fuori voce.)
        chiamate.append(1)
        if "sizing_context" not in run_offline.catturato:
            raise KeyError("synthetic summary failure")
        return vero(*args, **kwargs)
    monkeypatch.setattr(weekly_gaps, "committee_gaps_summary", guasto_prima_del_capo)
    result = cm.run_multi_agent(send_email=False)
    assert result["status"] == "completed", result.get("last_error")
    contesto = run_offline.catturato["sizing_context"]
    assert "RED TEAM MANCANTE" not in contesto and "COMITATO NON CALCOLATO" in contesto


from test_red_team_checkpoint import setup_provider, Crash, NATIVE_RED_TEAM  # noqa: E402


def test_R3_ripresa_da_checkpoint_running_col_catalogo_giu_e_lacuna(run_offline, monkeypatch):
    from bellomberg.agents import consigliere_multi as cm, weekly_lifecycle
    from test_weekly_recovery import _store
    inviate, tool_calls, _ = setup_provider(monkeypatch)
    native_bind = weekly_lifecycle.bind_blackboard
    crashed = []

    def bind(bb, store):
        native_bind(bb, store)
        persist = bb.persist_run_checkpoint

        def save(event, payload):
            persist(event, payload)
            if event == "red_team_tool" and not crashed:
                crashed.append(True)
                raise Crash("hard crash after durable Red Team tool result")
        bb.persist_run_checkpoint = save
    monkeypatch.setattr(weekly_lifecycle, "bind_blackboard", bind)
    with pytest.raises(Crash):
        cm.run_multi_agent(send_email=False)
    store = _store()
    assert len(inviate) == 1

    def giu(model):
        raise requests.ConnectionError("synthetic catalog down")
    monkeypatch.setattr(preparation_ai, "live_metadata", giu)
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert result["status"] == "completed", result.get("last_error")
    assert len(inviate) == 1                                      # nessun invio senza preventivo
    assert _store().get("red_team")["gap"].startswith("RedTeamSenzaPreventivo")


def test_metadati_modello_restituisce_una_copia(tmp_path):
    # R-0RT R2: chi riceve il listino non puo' alterare quello che il controllo prezzi usera'.
    registro = _registro(tmp_path, _listino)
    meta, gia_letto = registro.metadati_modello(MODELLO)
    assert gia_letto is False
    meta["top_provider"]["max_completion_tokens"] = 10**9
    meta["pricing"]["completion"] = "0"
    di_nuovo, gia_letto = registro.metadati_modello(MODELLO)
    assert gia_letto is True and di_nuovo == _listino(MODELLO)


def test_R3_ripresa_con_tetto_sceso_non_cambia_il_contratto_pagato(run_offline, monkeypatch):
    # Checkpoint 'running' a 128000 (gia' pagato un giro); alla ripresa il fornitore dichiara un
    # tetto piu' basso. MOD-CAP: il contratto salvato NON cambia e il giro pagato NON si rimanda
    # (nessun secondo corpo a 128000 ne' a 65536 per la stessa iterazione); il giro SUCCESSIVO,
    # mai inviato, parte col cap adattato e la critica si completa (niente lacuna).
    from bellomberg.agents import consigliere_multi as cm, weekly_lifecycle
    from test_weekly_recovery import _store
    inviate, tool_calls, _ = setup_provider(monkeypatch)
    native_bind = weekly_lifecycle.bind_blackboard
    crashed = []

    def bind(bb, store):
        native_bind(bb, store)
        persist = bb.persist_run_checkpoint

        def save(event, payload):
            persist(event, payload)
            if event == "red_team_tool" and not crashed:
                crashed.append(True)
                raise Crash("hard crash after durable Red Team tool result")
        bb.persist_run_checkpoint = save
    monkeypatch.setattr(weekly_lifecycle, "bind_blackboard", bind)
    with pytest.raises(Crash):
        cm.run_multi_agent(send_email=False)
    store = _store()
    assert [r["max_tokens"] for r in inviate] == [128000]
    monkeypatch.setattr(preparation_ai, "live_metadata", lambda model: {
        "id": model, "context_length": 1_000_000, "top_provider": {"max_completion_tokens": 65536},
        "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert result["status"] == "completed", result.get("last_error")
    assert [r["max_tokens"] for r in inviate] == [128000, 65536]  # giro 1 pagato una volta, giro 2 adattato
    assert len({json.dumps(r["messages"], sort_keys=True) for r in inviate}) == 2   # due giri diversi
    assert _store().get("red_team").get("gap") is None


def test_R3_checkpoint_del_codice_0506_col_cap_gia_tagliato_ripreso(run_offline, monkeypatch, capsys):
    # MOD-CAP: il codice 05-06/10 (_cap_del_provider) salvava il contratto al cap GIA' tagliato
    # (65536). Oggi il contratto e' 128000 e il taglio lo fa il client: lo stesso corpo sul filo.
    # Quel checkpoint resta riprendibile (nessun «contract changed»), dichiarato, e il giro gia'
    # pagato non si rimanda.
    from bellomberg.agents import consigliere_multi as cm, weekly_lifecycle
    from test_weekly_recovery import _store
    inviate, tool_calls, _ = setup_provider(monkeypatch)
    listino = lambda model: {"id": model, "context_length": 1_000_000,
                             "top_provider": {"max_completion_tokens": 65536},
                             "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
    monkeypatch.setattr(preparation_ai, "live_metadata", listino)
    monkeypatch.setattr(red_team, "WEEKLY_RED_MAX_TOKENS", 65536)      # = contratto del codice 05-06/10
    native_bind = weekly_lifecycle.bind_blackboard
    crashed = []

    def bind(bb, store):
        native_bind(bb, store)
        persist = bb.persist_run_checkpoint

        def save(event, payload):
            persist(event, payload)
            if event == "red_team_tool" and not crashed:
                crashed.append(True)
                raise Crash("hard crash after durable Red Team tool result")
        bb.persist_run_checkpoint = save
    monkeypatch.setattr(weekly_lifecycle, "bind_blackboard", bind)
    with pytest.raises(Crash):
        cm.run_multi_agent(send_email=False)
    store = _store()
    assert [r["max_tokens"] for r in inviate] == [65536]
    monkeypatch.setattr(red_team, "WEEKLY_RED_MAX_TOKENS", 128000)     # codice di oggi
    capsys.readouterr()
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert result["status"] == "completed", result.get("last_error")
    assert [r["max_tokens"] for r in inviate] == [65536, 65536]
    assert _store().get("red_team").get("gap") is None
    assert "checkpoint col cap gia' adattato al provider (65536" in capsys.readouterr().out


def test_R3_checkpoint_mai_inviato_misurato_sul_registro_ripreso_col_tetto(run_offline, monkeypatch):
    # e48d9c2 conservato: checkpoint a 128000, iterazione 0, NESSUNA richiesta _red_team nel
    # registro (crash prima dell'invio) -> alla ripresa si riparte col tetto del provider.
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.core import llm_client
    from test_weekly_recovery import _store
    inviate, tool_calls, client_creati = setup_provider(monkeypatch)
    fabbrica = llm_client.OpenRouterClient
    prima = []

    def crash_prima_dell_invio(**kwargs):
        if not prima:
            prima.append(True)

            class Morto:
                class messages:
                    @staticmethod
                    def create(**kw):
                        raise Crash("hard crash before the Red Team request reached the journal")
            return Morto()
        return fabbrica(**kwargs)
    monkeypatch.setattr(llm_client, "OpenRouterClient", crash_prima_dell_invio)
    with pytest.raises(Crash):
        cm.run_multi_agent(send_email=False)
    store = _store()
    assert inviate == []
    monkeypatch.setattr(preparation_ai, "live_metadata", lambda model: {
        "id": model, "context_length": 1_000_000, "top_provider": {"max_completion_tokens": 65536},
        "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert result["status"] == "completed", result.get("last_error")
    assert inviate and {r["max_tokens"] for r in inviate} == {65536}
    assert _store().get("red_team").get("gap") is None


def test_un_solo_meccanismo_del_tetto():
    # MOD-CAP: il taglio e la misura «mai inviato» del Red Team vivono nel punto centrale
    # (llm_client + RequestJournal.prepare forme_precedenti): nessun secondo meccanismo qui.
    assert not hasattr(red_team, "_cap_del_provider")
    assert not hasattr(red_team, "_red_team_mai_inviato")


# ------------------------------------------------------------ decisioni main 06/10 (punti 1-3)
# Goal PM: la run arriva in fondo (memo E PDF), ogni buco dichiarato; costo incerto dichiarato.

from test_consigliere_lacune import comitato, research_weekly, _html  # noqa: E402,F401


def _desk_a_costo_incerto(monkeypatch, desk="crypto", round_n=1):
    """Il desk manda UNA richiesta che finisce 503 (costo/esito incerto nel registro)."""
    import httpx
    from bellomberg.core.llm_client import OpenRouterClient
    from test_cablaggio_consigliere_multi import _DeskFinto
    monkeypatch.setattr(preparation_ai, "live_metadata", lambda model: {
        "id": model, "context_length": 1000, "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    inviate = []

    def send(request):
        inviate.append(request)
        return httpx.Response(503, json={"error": {"code": 503, "message": "synthetic provider outage"}})
    client = OpenRouterClient(api_key="test", max_retries=4, trasporto=httpx.MockTransport(send))
    originale = _DeskFinto.run

    def run(self, n):
        if self.name == desk and n == round_n:
            client.messages.create(model="test/model", max_tokens=20,
                                   messages=[{"role": "user", "content": "frozen weekly input"}])
        return originale(self, n)
    monkeypatch.setattr(_DeskFinto, "run", run)
    return inviate


def test_P1_ripresa_con_costo_incerto_prosegue_senza_reinvio(comitato, monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    from test_weekly_recovery import _store
    inviate = _desk_a_costo_incerto(monkeypatch)
    nativo = WeeklyRunStore.complete
    caduto = []

    def complete(self, name, *args, **kwargs):
        if name == "red_team" and not caduto:
            caduto.append(True)
            raise Crash("hard crash after the uncertain desk request")
        return nativo(self, name, *args, **kwargs)
    monkeypatch.setattr(WeeklyRunStore, "complete", complete)
    with pytest.raises(Crash):
        cm.run_multi_agent(send_email=False)
    store = _store()
    assert store.status()["request_costs"]["unknown_requests"] == 1
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert store.get("memo_validated") is not None, result.get("last_error")
    assert len(inviate) == 1                                       # la richiesta incerta NON si reinvia
    assert result["request_costs"]["unknown_requests"] == 1
    assert result["status"] == "incomplete" and result["analytical_status"] == "complete"


def test_P2_email_parte_col_costo_dichiarato_incerto(comitato, monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    observed = comitato[0]
    inviate = _desk_a_costo_incerto(monkeypatch)
    result = cm.run_multi_agent(send_email=True)
    assert len(inviate) == 1
    assert len(observed.inviati) == 1 and result["delivery_status"] == "sent"
    corpo = _html(observed.inviati[0])
    assert "COSTO DELLA RUN INCERTO" in corpo and "1 richiest" in corpo
    assert result["status"] == "incomplete"                      # costo da riconciliare: dichiarato


def test_P2_email_senza_costo_incerto_non_porta_la_riga(comitato):
    from bellomberg.agents import consigliere_multi as cm
    observed = comitato[0]
    cm.run_multi_agent(send_email=True)
    assert len(observed.inviati) == 1 and "COSTO DELLA RUN INCERTO" not in _html(observed.inviati[0])


def _spia_registro_decisioni(monkeypatch, cm):
    """Le fixture offline stubbano l'estrazione: il conteggio in tabella non basta. Si misura se il
    codice PROVA a registrare decisioni (gate col registro o nuovo tentativo in finalizzazione)."""
    chiamate = []
    for nome in ("_persist_and_read", "_finalize_publication_decisions"):
        vera = getattr(cm, nome)
        monkeypatch.setattr(cm, nome, lambda *a, _v=vera, _n=nome, **k: chiamate.append(_n) or _v(*a, **k))
    return chiamate


def _riepilogo_finale_guasto(monkeypatch, run_offline):
    from bellomberg.reporting import weekly_gaps
    vero = weekly_gaps.committee_gaps_summary

    def guasto_dopo_il_capo(*args, **kwargs):
        if kwargs.get("capo") is not None:
            raise KeyError("synthetic summary failure")
        return vero(*args, **kwargs)
    monkeypatch.setattr(weekly_gaps, "committee_gaps_summary", guasto_dopo_il_capo)


def test_P3_comitato_non_calcolato_dopo_il_capo_memo_e_pdf_senza_decisioni(legacy_red_team, run_offline,
                                                                          monkeypatch):
    from test_weekly_recovery import _store
    cm = legacy_red_team
    _riepilogo_finale_guasto(monkeypatch, run_offline)
    registrazioni = _spia_registro_decisioni(monkeypatch, cm)
    result = cm.run_multi_agent(send_email=True)
    assert registrazioni == []                                    # nessun tentativo di registrare
    store = _store()
    memo = store.get("memo_validated")["memo"]
    assert "decisioni non ammesse" in memo.lower()
    assert store.get("decisions_finalized")["ids"] == []
    with store.db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert any(row["kind"] == "PDF" for row in result["artifacts"])
    assert result["status"] == "incomplete" and result["analytical_status"] == "complete"
    assert "decisioni non ammesse" in str(result.get("incomplete_reason") or store.status().get("incomplete_reason")).lower()
    assert run_offline.inviati == []                              # email automatica non ammessa


def test_P3_ripresa_dopo_il_memo_non_registra_decisioni(comitato, run_offline, monkeypatch):
    # Research (la ripresa analitica di una run legacy e' vietata per contratto).
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    from test_weekly_recovery import _store
    _riepilogo_finale_guasto(monkeypatch, run_offline)
    nativo = WeeklyRunStore.complete
    caduto = []

    def complete(self, name, *args, **kwargs):
        risultato = nativo(self, name, *args, **kwargs)
        if name == "memo_validated" and not caduto:
            caduto.append(True)
            raise Crash("hard crash after the validated memo")
        return risultato
    monkeypatch.setattr(WeeklyRunStore, "complete", complete)
    registrazioni = _spia_registro_decisioni(monkeypatch, cm)
    with pytest.raises(Crash):
        cm.run_multi_agent(send_email=False)
    store = _store()
    result = cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert registrazioni == []
    assert store.get("decisions_finalized")["ids"] == []
    with store.db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert result["status"] == "incomplete"


# ------------------------------------------------------------ stato della run (main 06/10, ultimo giro)

# Importata QUI (raccolta): le fixture offline sostituiscono moduli (score_history) che l'API importa.
import bellomberg.api.bellomberg_api as _API  # noqa: E402


def _pagina(monkeypatch):
    """GET /agents/live sul heartbeat terminale VERO della run e sul DB della run (sola lettura)."""
    api = _API
    from bellomberg.agents.specialists.base import Blackboard
    from bellomberg.storage import memory_db
    monkeypatch.setattr(api, "AGENTS_LIVE_PATH", Blackboard.HEARTBEAT_PATH)
    monkeypatch.setattr(api, "SQLITE_PATH", memory_db.SQLITE_PATH)
    return api.get_agents_live()["esito_run"]


def test_S1_costo_incerto_ripresa_disponibile_e_costo_dichiarato_nel_motivo(comitato, monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _store
    _desk_a_costo_incerto(monkeypatch)
    cm.run_multi_agent(send_email=False)
    stato = _store().status()
    assert stato["request_costs"]["unknown_requests"] == 1
    assert stato["status"] == "incomplete" and stato["resume_available"] is True
    assert stato["blocked_reason"] is None
    assert "costi incerti" in stato["reason"].lower() and "ripresa disponibile" in stato["reason"]
    esito = _pagina(monkeypatch)
    # la pagina e il registro dicono la stessa cosa
    assert esito["fonte"] == "registro_run_settimanale"
    assert esito["stato"] == "incompleta"                          # non «bloccata»: niente e' bloccato
    assert esito["ripresa_disponibile"] is stato["resume_available"] is True
    assert esito["motivo"] == stato["reason"] and esito["memo_consegnato"] is True


def test_S1_registro_mancante_ripresa_non_disponibile_dichiarata(comitato, monkeypatch):
    # Senza registro la ripresa e' rifiutata da consigliere_multi («Registro richieste mancante»):
    # lo stato non la offre e dice perche'.
    from pathlib import Path
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _store
    _desk_a_costo_incerto(monkeypatch)
    cm.run_multi_agent(send_email=False)
    store = _store()
    Path(store.status()["request_journal_path"]).unlink()
    stato = store.status()
    assert stato["resume_available"] is False
    assert stato["blocked_reason"].startswith("Registro richieste non verificabile")


def test_S1_decisioni_non_ammesse_motivo_uguale_su_pagina_e_registro(comitato, run_offline, monkeypatch):
    from bellomberg.agents import consigliere_multi as cm
    from test_weekly_recovery import _store
    _riepilogo_finale_guasto(monkeypatch, run_offline)
    cm.run_multi_agent(send_email=False)
    stato = _store().status()
    esito = _pagina(monkeypatch)
    assert esito["stato"] == "incompleta"
    assert "decisioni non ammesse" in esito["motivo"] and esito["motivo"] == stato["reason"]


def test_S1_costo_incerto_dopo_la_chiusura_incompleta_non_bloccata(comitato, monkeypatch):
    # Una prenotazione incerta comparsa DOPO una run completata (crash a meta' di un invio):
    # la run diventa «incompleta» col costo dichiarato, non «bloccata».
    from pathlib import Path
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.core.request_journal import RequestJournal
    from test_weekly_recovery import _store
    result = cm.run_multi_agent(send_email=False)
    assert result["status"] == "completed"
    store = _store()
    RequestJournal(Path(store.status()["request_journal_path"]), run_id=store.run_id,
                   authorization={"scope": "weekly", "memo_id": store.memo_id,
                                  "context_sha256": result["context_sha256"], "authorized_usd": None},
                   metadata=lambda model: {"id": model, "context_length": 1000,
                                           "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
                   ).prepare({"model": "test/model", "max_tokens": 20,
                              "messages": [{"role": "user", "content": "reserved before a hard crash"}]},
                             {"phase": "synthetic_crash"})
    stato = store.status()
    assert stato["status"] == "incomplete" and stato["operational_status"] == "requires_pm_review"
    assert "costi incerti" in stato["reason"].lower()
