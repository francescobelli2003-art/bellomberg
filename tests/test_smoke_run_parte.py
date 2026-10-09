# -*- coding: utf-8 -*-
"""Smoke «la run parte?» SENZA SPESA (voce 7 handoff-4, 05/10/2026, Opus 5.5).

Il 05/10 sera la run settimanale e' morta 5 volte in un'ora su comportamenti del fornitore
(ragionamento obbligatorio di Muse e Opus 5.5, `:exacto` senza listino e con risposta col nome
base, Red Team con max_tokens sopra il tetto del provider): i test, tutti con client finti di
alto livello, erano verdi. Qui il client e' quello VERO (llm_client, sonda dei modelli, journal
delle richieste, streaming SSE) e il finto sta al livello del TRASPORTO HTTP: il fornitore finto
(tests/fornitore_openrouter_finto.py) rifiuta cio' che rifiuterebbe OpenRouter, leggendo i limiti
dalla fixture della Models API (tests/fixtures/openrouter_models_snapshot.json).

Tre prove:
  1. `test_slug_passa_dal_fornitore`: OGNI variabile *_MODEL del .env.example (e del .env del PM,
     letto SOLO per i nomi degli slug) passa sonda + chiamata stream + chiamata non-stream sotto
     journal, con le richieste di ragionamento che il codice manda davvero (spento/acceso/minimal);
  2. `test_run_settimanale_arriva_al_capo_e_al_pdf`: la run settimanale VERA (desk, Red Team, Capo,
     publication gate, decisioni, reflection, renderer PDF, decisione d'invio) gira in un processo
     isolato fino alla consegna, con gli slug del .env.example (e del .env). Obiettivo PM «memo E PDF
     senza blocchi»: sonda tutta OK, nessun rifiuto del fornitore, nessuna richiesta incerta, Red
     Team senza lacuna, tutti i desk completi, run completata, PDF leggibile (pypdf), memo nel DB
     non piu' [IN PROGRESS], una email decisa e consegnata all'SMTP finto col PDF allegato;
  3. `test_ripresa_col_checkpoint_red_team_al_cap_vecchio` (e48d9c2): checkpoint del Red Team
     salvato al cap 128000 dal codice delle 21:00 -> la ripresa col codice attuale arriva al PDF.
Finti e dichiarati: i tool di DATI (macro, rischio, notizie, prezzi, scorekeeper; elenco in
TOOL_DATI_FINTI) e l'SMTP. Ticker e importi inventati."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import fornitore_openrouter_finto as ff

RADICE = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = RADICE / ".env.example"
ENV_PM = RADICE / ".env"
CATALOGO, DOC = ff.carica_catalogo()


def _slug_per_variabile():
    """[(origine, VARIABILE, slug)] di ogni *_MODEL valorizzata. Dal .env del PM si leggono SOLO
    righe *_MODEL (slug pubblici): nessuna chiave entra nei test."""
    righe = [("env_example", nome, slug) for nome, slug in sorted(ff.variabili_env(ENV_EXAMPLE).items())]
    righe += [("env_pm", nome, slug) for nome, slug in sorted(ff.variabili_env(ENV_PM).items())]
    return [r for r in righe if r[2]]


_SLUG = _slug_per_variabile()


def test_la_fixture_copre_ogni_slug_configurato():
    """Uno slug nuovo nel .env.example senza riga nella fixture = prova muta: va DICHIARATO."""
    assert _SLUG, "nessuna variabile *_MODEL valorizzata nel .env.example"
    mancanti = sorted({slug for _, _, slug in _SLUG if ff.metadati(CATALOGO, slug)[0] is None})
    assert not mancanti, ("slug senza metadati nella fixture (rifare la GET gratuita della Models "
                          "API e aggiornare openrouter_models_snapshot.json): " + ", ".join(mancanti))
    assert DOC.get("_data_lettura") and DOC.get("_fonte", "").startswith("GET https://openrouter.ai/api/v1/models")


# ----------------------------------------------------------------------------- 0. il fornitore finto
# Il finto vale quanto la sua fedelta': queste prove fissano i tre comportamenti del 05/10.

def _post(fornitore, **corpo):
    import httpx
    corpo.setdefault("messages", [{"role": "user", "content": "ping"}])
    with httpx.Client(transport=fornitore.trasporto()) as c:
        return c.post("https://openrouter.ai/api/v1/chat/completions", json=corpo)


def test_fornitore_finto_rifiuta_il_ragionamento_spento_dove_la_models_api_lo_dice_obbligatorio():
    fornitore = ff.FornitoreFinto(CATALOGO)
    obbligatori = sorted(m for m, r in CATALOGO.items() if r["reasoning"]["mandatory"] is True)
    facoltativi = sorted(m for m, r in CATALOGO.items() if r["reasoning"]["mandatory"] is not True)
    assert "meta/muse-spark-1.3" in obbligatori and "anthropic/claude-opus-5.5" in obbligatori
    assert facoltativi, "nessun modello a ragionamento facoltativo: la prova non distingue"
    for m in obbligatori:
        r = _post(fornitore, model=m, max_tokens=16, reasoning={"enabled": False})
        assert r.status_code == 400 and "Reasoning is mandatory" in r.text, (m, r.text)
        assert _post(fornitore, model=m, max_tokens=16, reasoning={"effort": "minimal"}).status_code == 200, m
    for m in facoltativi:
        assert _post(fornitore, model=m, max_tokens=16, reasoning={"enabled": False}).status_code == 200, m


def test_fornitore_finto_rifiuta_max_tokens_oltre_il_tetto_del_provider():
    fornitore = ff.FornitoreFinto(CATALOGO)
    for m, riga in CATALOGO.items():
        tetto = riga["top_provider"]["max_completion_tokens"]
        assert _post(fornitore, model=m, max_tokens=tetto).status_code == 200, m
        r = _post(fornitore, model=m, max_tokens=tetto + 1)
        assert r.status_code == 400 and str(tetto) in r.text, (m, r.text)
    assert CATALOGO["google/gemini-3.8-flash"]["top_provider"]["max_completion_tokens"] == 65536


def test_fornitore_finto_la_variante_exacto_risponde_col_nome_base_e_non_ha_listino(monkeypatch):
    fornitore = ff.FornitoreFinto(CATALOGO)
    varianti = DOC["_varianti_non_in_catalogo"]
    assert varianti and all(v not in CATALOGO for v in varianti)
    for v in varianti:
        r = _post(fornitore, model=v, max_tokens=16)
        assert r.status_code == 200 and r.json()["model"] == v.partition(":")[0], r.text
    import requests
    ff.monta_requests_models(ff.AdattatoreModels(DOC), monkeypatch.setattr)
    ids = {riga["id"] for riga in requests.get(ff.URL_MODELS, timeout=5).json()["data"]}
    assert set(CATALOGO) == ids and not ids & set(varianti)


# ----------------------------------------------------------------------------- 1. per slug

@pytest.fixture
def senza_rete(monkeypatch):
    import socket
    violazioni = []

    def vieta(host):
        if str(host) in ff._LOCALI or str(host).startswith("127."):
            return
        violazioni.append(str(host))
        raise ConnectionError("rete vera nello smoke: %r" % (host,))

    monkeypatch.setattr(socket.socket, "connect",
                        lambda s, a, _o=socket.socket.connect: (vieta(a[0] if isinstance(a, tuple) else a), _o(s, a))[1])
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda h, *a, _o=socket.getaddrinfo, **k: (vieta(h), _o(h, *a, **k))[1])
    yield violazioni
    assert not violazioni, violazioni


@pytest.mark.parametrize("origine,variabile,slug", _SLUG, ids=["%s:%s" % (o, v) for o, v, _ in _SLUG])
def test_slug_passa_dal_fornitore(origine, variabile, slug, tmp_path, monkeypatch, senza_rete):
    """Lo slug di `variabile` attraversa il client VERO e il journal VERO col fornitore finto:
    sonda (effort minimal, max_tokens della sonda), una chiamata col ragionamento richiesto SPENTO
    (reflection, filing, desk in ripiego) e una in streaming ACCESO (desk, Capo, chat). Il 05/10
    Muse/Opus 5.5 (spento -> 400), `:exacto` (listino e identita') cadevano qui."""
    from bellomberg.core import llm_client
    from bellomberg.core.request_journal import RequestJournal, request_scope
    fornitore = ff.FornitoreFinto(CATALOGO)
    ff.monta_trasporto(llm_client, fornitore, monkeypatch.setattr)
    ff.monta_requests_models(ff.AdattatoreModels(DOC), monkeypatch.setattr)
    journal = RequestJournal(tmp_path / "smoke-requests.sqlite", run_id="smoke-" + variabile,
                             authorization={"scope": "smoke", "variabile": variabile, "authorized_usd": None})
    with request_scope(journal, phase="model_probe", agent="_probe"):
        esiti = llm_client.sonda_modelli([slug])
    assert esiti[slug]["ok"] is True, (variabile, slug, esiti[slug]["motivo"], fornitore.rifiuti())
    client = llm_client.OpenRouterClient(max_retries=0)
    with request_scope(journal, phase="smoke", agent="spento"):
        msg = client.messages.create(model=slug, max_tokens=1000, thinking={"type": "disabled"},
                                     messages=[{"role": "user", "content": "riassumi ZZSMOKE.MI"}])
    assert msg.stop_reason == "end_turn" and msg.content, (variabile, slug, fornitore.rifiuti())
    with request_scope(journal, phase="smoke", agent="acceso"):
        with client.messages.stream(model=slug, max_tokens=1000, thinking={"type": "adaptive"},
                                    messages=[{"role": "user", "content": "analizza QQSYN.MI"}]) as s:
            msg = s.get_final_message()
    assert msg.stop_reason == "end_turn" and msg.content, (variabile, slug, fornitore.rifiuti())
    riepilogo = journal.summary()
    assert not riepilogo["unknown_requests"], (variabile, slug, riepilogo)
    assert not fornitore.rifiuti(), fornitore.rifiuti()
    assert len(fornitore.registro) == 3


# ----------------------------------------------------------------------------- 2. run intera

def _config_runs():
    """Le configurazioni da far girare: il .env.example cosi' com'e', e (se esiste) il .env.example
    con gli SLUG del .env del PM al posto dei suoi (solo *_MODEL: nessun altro valore del PM)."""
    esempio = ff.variabili_env(ENV_EXAMPLE, suffisso="")
    pm = ff.variabili_env(ENV_PM)
    if not pm:
        return [pytest.param(esempio, id="env_example")]
    con_pm = {**esempio, **pm}
    if con_pm == esempio:   # stessi slug: una run sola copre entrambi (dichiarato nell'id)
        return [pytest.param(esempio, id="env_example=env_pm_slug")]
    return [pytest.param(esempio, id="env_example"), pytest.param(con_pm, id="env_pm_slug")]


def _ambiente(variabili, tmp):
    """Ambiente del processo della run: NESSUNA variabile ereditata coi nomi del .env.example o
    del .env (la run legge solo cio' che passa il test), chiave finta, dati in tmp."""
    nomi_da_togliere = set(ff.variabili_env(ENV_EXAMPLE, suffisso="")) | set(ff.variabili_env(ENV_PM, suffisso=""))
    env = {k: v for k, v in os.environ.items() if k not in nomi_da_togliere}
    env.update({k: v for k, v in variabili.items()})
    env.update({
        "OPENROUTER_API_KEY": "sk-or-finto-smoke-senza-spesa",
        "BELLOMBERG_DATA_DIR": str(tmp / "data"),
        "BELLOMBERG_REPORT_DIR": str(tmp / "report"),
        "BELLOMBERG_RESEARCH_NOTES_DIR": str(tmp / "research_notes"),
        "BELLOMBERG_PROJECT_ROOT": "",
        # email configurata con valori FINTI: decide il codice vero, consegna all'SMTP finto del runner
        "EMAIL_FROM": "smoke-da@example.com", "EMAIL_PASSWORD": "password-finta-smoke",
        "EMAIL_TO": "smoke-a@example.com", "EMAIL_SEND_ATTEMPTS": "1",
        "SEC_CONTACT_EMAIL": "smoke@example.com",
        "NEWS_AUTO_REFRESH_ENABLED": "false", "FILING_AUTO_REFRESH_ENABLED": "false",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(RADICE / "src"),
    })
    return env


def esegui_run_isolata(variabili, tmp, radice=RADICE, timeout=600, nome="esito", **opzioni):
    """La run in un processo separato: i percorsi runtime (heartbeat, lock, DB, journal, mandato)
    si risolvono all'import da BELLOMBERG_DATA_DIR, quindi il processo nasce gia' in tmp."""
    for d in ("data", "report", "research_notes"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    uscita = tmp / (nome + ".json")
    config = tmp / (nome + "-config.json")
    config.write_text(json.dumps({"radice": str(radice), "tmp": str(tmp), "uscita": str(uscita),
                                  "fixture": str(ff.FIXTURE), **opzioni}), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-B", str(Path(ff.__file__).resolve()), str(config)],
                          cwd=str(tmp), env=_ambiente(variabili, tmp), capture_output=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    (tmp / (nome + ".log")).write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    assert uscita.is_file(), ("la run non ha scritto l'esito (exit %s): %s" % (proc.returncode, proc.stderr[-3000:]))
    return json.loads(uscita.read_text(encoding="utf-8")), proc


def problemi_della_run(esito):
    """Le ragioni per cui la run NON e' partita pulita, in frasi leggibili (vuoto = ok)."""
    problemi = []
    # Rete: i tool di dati veri ci provano e l'audit hook li ferma (rifiuto dichiarato al desk):
    # bloccata = ok. Il data/ vero o una scrittura nell'albero invece sono un guasto della prova.
    guasti = [v for v in esito.get("violazioni") or [] if not v.startswith(("rete:", "dns:"))]
    if guasti:
        problemi.append("guardie violate (data/ vero o albero): " + "; ".join(guasti[:5]))
    if not esito.get("arrivato_al_capo"):
        problemi.append("la run non e' arrivata al memo del Capo: " + str(esito.get("eccezione")))
    rifiuti = [r for r in esito.get("fornitore", []) if r.get("status") != 200]
    for r in rifiuti[:6]:
        problemi.append("fornitore: HTTP %s su %s (max_tokens %s, reasoning %s): %s" % (
            r["status"], r["model"], r.get("max_tokens"), r.get("reasoning"), r.get("motivo")))
    sonda_ko = [k for k in esito.get("tool_health_ko") or [] if str(k).startswith("modello ")]
    if sonda_ko:
        problemi.append("sonda modelli KO: " + " | ".join(sonda_ko))
    costi = esito.get("costi_journal") or {}
    if costi.get("unknown_requests"):
        problemi.append("journal: %s richieste con costo/esito incerto" % costi["unknown_requests"])
    if costi.get("errore"):
        problemi.append("journal illeggibile: " + str(costi["errore"]))
    if esito.get("red_team_gap"):
        problemi.append("Red Team in lacuna: " + str(esito["red_team_gap"]))
    elif not esito.get("red_team_completato"):
        problemi.append("Red Team non completato")
    for desk, motivo in (esito.get("desk_gaps") or {}).items():
        problemi.append("desk %s in lacuna: %s" % (desk, motivo))
    if "desk_mancanti" not in esito:
        problemi.append("run non creata (nessuno stato di run da leggere)")
    elif esito.get("desk_mancanti"):
        problemi.append("report di desk non completati: " + ", ".join(esito["desk_mancanti"]))
    if esito.get("arrivato_al_capo"):
        if (esito.get("usage_capo") or {}).get("complete") is not True:
            problemi.append("Capo non completo: " + str(esito.get("usage_capo")))
        if "ZZSMOKE.MI" not in str(esito.get("memo")):
            problemi.append("memo del Capo senza il testo del fornitore finto")
    return problemi


def problemi_della_consegna(esito, tmp):
    """Memo E PDF senza blocchi (obiettivo PM): run completata, PDF leggibile, memo pubblicato, email decisa."""
    problemi = []
    risultato = esito.get("risultato") or {}
    if risultato.get("status") != "completed" or risultato.get("analytical_status") != "complete":
        problemi.append("run non completata: %s / %s (errore: %s)" % (
            risultato.get("status"), risultato.get("analytical_status"), risultato.get("last_error")))
    pdf = [a for a in risultato.get("artifacts") or [] if a.get("kind") == "PDF" and a.get("role") == "memo"]
    if len(pdf) != 1:
        problemi.append("PDF del memo assente fra gli artefatti: " + str(risultato.get("artifacts")))
    else:
        percorso = Path(pdf[0]["path"])
        if not str(percorso.resolve()).startswith(str(tmp.resolve())):
            problemi.append("PDF fuori dalla cartella temporanea: " + str(percorso))
        try:
            from pypdf import PdfReader
            pagine = PdfReader(str(percorso)).pages
            testo = "".join((p.extract_text() or "") for p in pagine)
            if not pagine or "ZZSMOKE" not in testo:
                problemi.append("PDF illeggibile o senza il memo (%d pagine)" % len(pagine))
        except Exception as exc:
            problemi.append("PDF non leggibile da pypdf: " + type(exc).__name__ + ": " + str(exc)[:200])
    import sqlite3
    db = tmp / "data" / "consigliere.db"
    with sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True) as conn:
        riga = conn.execute("SELECT full_markdown, pdf_path FROM memos WHERE id=?",
                            (esito.get("memo_id"),)).fetchone()
    if riga is None or not riga[0] or riga[0].strip().startswith("[IN PROGRESS]"):
        problemi.append("memo nel DB ancora [IN PROGRESS] o assente")
    if risultato.get("delivery_status") != "sent" or len(esito.get("email_inviate") or []) != 1:
        problemi.append("email non decisa/consegnata una volta: %s, %s" % (
            risultato.get("delivery_status"), esito.get("email_inviate")))
    elif not any(str(n).endswith("_memo.pdf") for n in esito["email_inviate"][0]["allegati"]):
        problemi.append("email senza il PDF del memo: " + str(esito["email_inviate"][0]["allegati"]))
    return problemi


@pytest.mark.parametrize("variabili", _config_runs())
def test_run_settimanale_arriva_al_capo_e_al_pdf(variabili, tmp_path):
    """Run VERA dall'avvio alla consegna: sonda, R0/R1, Red Team, R2, Capo, publication gate,
    decisioni, reflection, PDF (renderer di produzione), decisione d'invio (SMTP finto)."""
    esito, proc = esegui_run_isolata(variabili, tmp_path, fino="pdf", send_email=True)
    problemi = problemi_della_run(esito) + problemi_della_consegna(esito, tmp_path)
    assert not problemi, ("LA RUN NON ARRIVA AL PDF (log: %s):\n- " % (tmp_path / "esito.log")) + "\n- ".join(problemi)
    # Gli slug possono coincidere: la reflection si prova dal ruolo e dai checkpoint persistiti.
    modelli = {r["model"] for r in esito["fornitore"]}
    attesi = {variabili[v] for v in ("CONSIGLIERE_R0_MODEL", "CAPO_MODEL", "RED_TEAM_MODEL",
                                      "REFLECTION_MODEL", "ACTION_EXTRACTOR_MODEL")}
    assert attesi <= modelli, sorted(attesi - modelli)
    _verifica_reflection(esito, tmp_path)
    assert esito["letture_models"] >= 1   # listino e tetti letti dalla Models API (finta)
    # Il giro dei tool e' passato dal filo (chiamata tool in stream, risultato, report).
    assert any(r.get("tool") and r["stream"] for r in esito["fornitore"]), "nessuna chiamata tool in streaming"
    assert esito["usage_capo"]["stop_reason"] == "end_turn"


def _verifica_reflection(esito, tmp, esclusione=None):
    """Nessuna lezione senza esiti attestati; quella generata resta legata al gruppo sul DB."""
    import sqlite3
    from bellomberg.core.reflection_policy import verified_lesson
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.storage.weekly_run_store import WeeklyRunStore

    percorso = tmp / "data" / "consigliere.db"
    with sqlite3.connect("file:" + percorso.as_posix() + "?mode=ro", uri=True) as conn:
        checkpoints = {stage: json.loads(payload) for stage, payload in conn.execute(
            "SELECT stage, payload_json FROM weekly_checkpoints WHERE memo_id=?",
            (esito["memo_id"],))}
    evidence = checkpoints["reflection_input_v1"]
    result = checkpoints["reflection"]
    richieste = [r for r in esito["fornitore"] if r.get("phase") == "reflection"]
    # Riapertura del ledger: prova anche hash e coerenza semantica dei checkpoint salvati.
    db = MemoryDB.__new__(MemoryDB)
    db.db_path = str(percorso)  # lettore reale, senza reinizializzare schema/Chroma del DB figlio
    lezione = verified_lesson(WeeklyRunStore(db, esito["memo_id"]))
    if esclusione:
        assert evidence["groups"] == {} and evidence["excluded"] == [esclusione]
        assert evidence["request"] is None and richieste == []
        assert result["status"] == "not_generated" and result["cause"] == "NO_ELIGIBLE_GROUPS"
        assert result["rows"] == [] and result["lesson"] == "" and lezione is None
        return
    assert len(richieste) == 1, "la reflection non e' passata una volta dal trasporto"
    chiamata = richieste[0]
    assert chiamata["agent"] == "_reflection" and chiamata["status"] == 200
    assert chiamata["model"] == evidence["request"]["model"]
    assert chiamata["reflection_groups"] == evidence["groups"]
    capo = [r for r in esito["fornitore"] if r.get("phase") == "capo" and r.get("agent") == "capo"]
    assert capo and chiamata["n"] > max(r["n"] for r in capo)
    assert evidence["groups"]["overall"] == {
        "n": 36, "hits": 24, "hit_rate_pct": 66.7, "avg_edge_pct": 1.23,
        "decision_ids": list(range(1, 37)),
    }
    assert result["status"] == "generated" and result["cause"] is None
    assert len(result["rows"]) == 1 and result["rows"][0]["group_ids"] == ["overall"]
    assert "[src: scorekeeper]" in result["rows"][0]["instruction"]
    assert lezione == result["lesson"] and lezione


@pytest.mark.parametrize("scenario,esclusione", [
    ("sotto_soglia", "overall:BELOW_36"),
    ("dettagli_assenti", "MISSING_OUTCOME_DETAILS"),
])
def test_reflection_non_qualificata_non_chiama_provider_ma_consegna_pdf(scenario, esclusione, tmp_path):
    esempio = ff.variabili_env(ENV_EXAMPLE, suffisso="")
    esito, _ = esegui_run_isolata(esempio, tmp_path, fino="pdf", send_email=True,
                                 reflection_scenario=scenario)
    problemi = problemi_della_run(esito) + problemi_della_consegna(esito, tmp_path)
    assert not problemi, problemi
    _verifica_reflection(esito, tmp_path, esclusione=esclusione)


def test_ripresa_col_checkpoint_red_team_al_cap_vecchio(tmp_path):
    """e48d9c2 (ripresa del memo 67, 05/10 21:10): il tentativo delle 21:00 aveva salvato il
    checkpoint `red_team_ready` all'iterazione 0 col cap 128000, poi il controllo prezzi l'aveva
    rifiutato PRIMA dell'invio. Primo processo = il codice delle 21:00 (red_cap_vecchio: niente
    taglio al tetto del provider, niente preventivo anticipato) -> run ferma al Red Team col
    checkpoint mai inviato; secondo processo = codice attuale, ripresa autorizzata -> deve
    riprendere col tetto del provider e arrivare al PDF."""
    esempio = ff.variabili_env(ENV_EXAMPLE, suffisso="")
    prima, _ = esegui_run_isolata(esempio, tmp_path, nome="prima", fino="pdf", send_email=True,
                                  red_cap_vecchio=True)
    assert "requested completion cap exceeds" in str(prima.get("eccezione")), prima.get("eccezione")
    salvato = (prima.get("checkpoint_red_team") or {}).get("red_team:R1") or {}
    assert salvato == {"max_tokens": 128000, "iteration": 0, "status": "running", "calls": 0}, prima.get("checkpoint_red_team")
    assert (prima.get("stato_run") or {}).get("status") != "completed"
    assert not [r for r in prima["fornitore"] if r["model"] == esempio["RED_TEAM_MODEL"] and r["max_tokens"] == 128000]

    ripresa, proc = esegui_run_isolata(esempio, tmp_path, nome="esito", fino="pdf", send_email=True,
                                       ripresa_memo_id=prima["memo_id"])
    problemi = problemi_della_run(ripresa) + problemi_della_consegna(ripresa, tmp_path)
    assert not problemi, ("LA RIPRESA NON ARRIVA AL PDF (log: %s):\n- " % (tmp_path / "esito.log")) + "\n- ".join(problemi)
    assert ripresa["memo_id"] == prima["memo_id"]
    tetto = CATALOGO[esempio["RED_TEAM_MODEL"].partition(":")[0]]["top_provider"]["max_completion_tokens"]
    red = [r for r in ripresa["fornitore"] if r["model"] == esempio["RED_TEAM_MODEL"] and r["max_tokens"] != 2048]
    assert red and all(r["max_tokens"] == tetto for r in red), red
    # MOD-CAP: il contratto salvato (128000, mai inviato) resta quello; il client lo adatta sul filo.
    assert ("red_team/_red_team/R1: richiesti 128000 token, %s ne accetta %d: uso %d"
            % (esempio["RED_TEAM_MODEL"], tetto, tetto)) in proc.stdout, proc.stdout[-3000:]


# ----------------------------------------------------------------------------- 3. tetto adattivo
GEMINI_FLASH = "google/gemini-3.8-flash"


def test_run_settimanale_tutti_i_ruoli_su_gemini_flash_arriva_al_pdf(tmp_path):
    """MOD-CAP (06/10, decisione PM «di default 128.000, ma se lancio un modello con un tetto minore
    si adatta in automatico»): chi scarica la repo e mette gemini-3.8-flash (tetto di uscita 65536
    nella Models API) in TUTTI i ruoli deve arrivare al PDF. Prima: ogni chiamata a 128000 era
    rifiutata dal controllo prezzi (desk, Red Team, Capo). Nessuna richiesta oltre il tetto arriva
    al fornitore; l'adattamento e' DICHIARATO una volta per ruolo."""
    tetto = CATALOGO[GEMINI_FLASH]["top_provider"]["max_completion_tokens"]
    assert tetto < 128000
    esempio = ff.variabili_env(ENV_EXAMPLE, suffisso="")
    tutti = {k: (GEMINI_FLASH if k.endswith("_MODEL") and v else v) for k, v in esempio.items()}
    esito, proc = esegui_run_isolata(tutti, tmp_path, fino="pdf", send_email=True)
    problemi = problemi_della_run(esito) + problemi_della_consegna(esito, tmp_path)
    assert not problemi, ("TUTTO GEMINI FLASH NON ARRIVA AL PDF (log: %s):\n- " % (tmp_path / "esito.log")) + "\n- ".join(problemi)
    filo = esito["fornitore"]
    assert filo and {r["model"] for r in filo} == {GEMINI_FLASH}
    _verifica_reflection(esito, tmp_path)
    assert max(r["max_tokens"] for r in filo) == tetto      # i 128000 sono arrivati come 65536
    for ruolo in ("capo/capo/R3", "red_team/_red_team/R1"):
        assert ("%s: richiesti 128000 token, %s ne accetta %d: uso %d" % (ruolo, GEMINI_FLASH, tetto, tetto)
                in proc.stdout), (ruolo, proc.stdout[-3000:])
    assert "ne accetta %d" % tetto in proc.stdout and "SONDA" in proc.stdout
    assert "tetto di uscita %d" % tetto in proc.stdout       # la sonda riporta tetto e adattamento
