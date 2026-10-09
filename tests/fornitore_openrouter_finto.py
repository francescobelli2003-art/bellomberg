# -*- coding: utf-8 -*-
"""Fornitore OpenRouter FINTO al livello del TRASPORTO HTTP (voce 7 handoff-4, 05/10/2026, Opus 5.5).

Il 05/10 sera la run settimanale e' morta 5 volte su comportamenti del FORNITORE che nessun
client finto di alto livello poteva vedere:
  1. Muse rifiuta `reasoning: {"enabled": false}` (HTTP 400 «Reasoning is mandatory»), 19:26;
  2. Opus 5.5 idem, 19:58 (cura di Muse troppo stretta);
  3. `...:exacto` non e' nel listino /models: prezzo non trovato / «pricing model mismatch»;
  4. `...:exacto` risponde col nome del modello BASE: il journal vedeva «un altro modello», 20:19;
  5. Red Team con max_tokens 128000 oltre il tetto del provider (65536 di gemini-3.8-flash), 21:00.
Qui il fornitore li RIPRODUCE leggendo i limiti dalla fixture `openrouter_models_snapshot.json`
(copia ridotta della Models API, GET gratuita): nessuna regola scritta a memoria per un modello.

Due superfici, entrambe di trasporto:
  - POST /chat/completions -> `httpx.MockTransport(fornitore.gestisci)` (llm_client vero, sonda vera,
    journal vero, streaming SSE vero);
  - GET /models -> adattatore `requests` (preparation_ai.live_metadata vero, tetto di uscita del client vero:
    llm_client.tetto_uscita, MOD-CAP 06/10).

Eseguito come script (`python -B fornitore_openrouter_finto.py <config.json>`) fa girare la run
settimanale VERA fino al Capo compreso in un processo isolato: BELLOMBERG_DATA_DIR in una cartella
temporanea PRIMA di ogni import (heartbeat, lock, journal, DB, mandato), rete bloccata da un audit
hook, scritture nell'albero e nel data/ vero vietate dallo stesso hook. I tool di DATI (macro,
rischio, notizie, prezzi...) sono finti e DICHIARATI in `TOOL_DATI_FINTI`; tutto il percorso LLM e'
quello di produzione. Ticker e importi inventati (ZZSMOKE.MI, 123.45)."""
import json
import os
import sys
import threading
import time
from pathlib import Path

FIXTURE = Path(__file__).with_name("fixtures") / "openrouter_models_snapshot.json"
URL_MODELS = "https://openrouter.ai/api/v1/models"
VARIANTI = ("exacto", "nitro", "floor")   # instradamento OpenRouter: rispondono col nome base
MSG_RAGIONAMENTO = "Reasoning is mandatory for this endpoint and cannot be disabled."

# I tool di DATI sostituiti nella run (mai un modello, mai il client, mai il journal):
TOOL_DATI_FINTI = (
    "macro_dashboard", "polymarket", "hyperliquid", "var_contribution/nav_history",
    "portfolio_metrics/reconcile_betas", "news_feed", "twr_engine (riconciliazione NAV)",
    "freshness", "scorekeeper", "portfolio_risk", "montecarlo", "sizing_engine",
    "correlazione", "archivio filing", "chroma (memoria vettoriale)",
    "yfinance/curl_cffi (vietati)", "current_facts (blocchi di contesto)",
)


def carica_catalogo(path=FIXTURE):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return {riga["id"]: riga for riga in doc["data"]}, doc


def variabili_env(path, suffisso="_MODEL"):
    """{VARIABILE: valore} delle righe NON commentate che finiscono per `suffisso`.
    Per il .env del PM si chiama SOLO con suffissi di slug/tetti: le chiavi non si leggono."""
    out = {}
    p = Path(path)
    if not p.is_file():
        return out
    for riga in p.read_text(encoding="utf-8").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#") or "=" not in riga:
            continue
        nome, _, valore = riga.partition("=")
        nome = nome.strip()
        if nome.endswith(suffisso) and nome.replace("_", "").isalnum() and nome.upper() == nome:
            out[nome] = valore.split("#", 1)[0].strip().strip('"').strip("'")
    return out


def metadati(catalogo, slug):
    """La riga del catalogo per uno slug, come la vede il FORNITORE: la variante di
    instradamento usa il modello base (non e' nel catalogo)."""
    if slug in catalogo:
        return catalogo[slug], slug
    base, _, variante = str(slug).partition(":")
    if variante in VARIANTI and base in catalogo:
        return catalogo[base], base
    return None, None


class FornitoreFinto:
    """Il comportamento del fornitore che ha fermato la run il 05/10, dai metadati della fixture."""

    def __init__(self, catalogo, testo_per=None):
        self.catalogo = catalogo
        self.registro = []          # una riga per richiesta: modello, stream, esito, motivo
        self._lock = threading.Lock()
        self._n = 0
        self.testo_per = testo_per or testo_sintetico

    # ------------------------------------------------------------------ regole
    def rifiuto(self, corpo):
        """(status, messaggio) se il fornitore vero rifiuterebbe la richiesta, altrimenti None."""
        modello = corpo.get("model")
        meta, _ = metadati(self.catalogo, modello)
        if meta is None:
            return 404, "No endpoints found for " + str(modello) + " (non nel catalogo della fixture)"
        ragionamento = corpo.get("reasoning")
        if ((meta.get("reasoning") or {}).get("mandatory") is True and isinstance(ragionamento, dict)
                and ragionamento.get("enabled") is False):
            return 400, MSG_RAGIONAMENTO
        tetto = (meta.get("top_provider") or {}).get("max_completion_tokens")
        richiesti = corpo.get("max_tokens")
        if isinstance(tetto, int) and isinstance(richiesti, int) and richiesti > tetto:
            return 400, ("max_tokens " + str(richiesti) + " exceeds the maximum completion tokens ("
                         + str(tetto) + ") for " + str(modello))
        provider = corpo.get("provider") or {}
        if provider.get("require_parameters") is True:
            ammessi = set(meta.get("supported_parameters") or ())
            chiesti = {k for k in ("tools", "tool_choice", "reasoning", "response_format") if k in corpo}
            if chiesti - ammessi:
                return 404, "No endpoints found that support the requested parameters"
        prezzo = provider.get("max_price") or {}
        for chiave in ("prompt", "completion"):
            listino = float(meta["pricing"][chiave]) * 10 ** 6
            if chiave in prezzo and float(prezzo[chiave]) + 1e-12 < listino:
                return 404, "No endpoints found matching your max_price"
        return None

    # ------------------------------------------------------------------ risposte
    def _usage(self, corpo, testo, meta):
        prompt = max(1, len(json.dumps(corpo.get("messages"), ensure_ascii=False)) // 4)
        completion = max(1, len(testo) // 4)
        costo = prompt * float(meta["pricing"]["prompt"]) + completion * float(meta["pricing"]["completion"])
        return {"prompt_tokens": prompt, "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
                "cost": round(costo, 12)}

    def gestisci(self, request):
        import httpx
        from bellomberg.core.request_journal import current_request_scope
        corpo = json.loads(request.content.decode("utf-8"))
        scope = current_request_scope() or {}
        ruolo = {k: scope.get(k) for k in ("phase", "agent", "round_n")}
        modello = corpo.get("model")
        stream = bool(corpo.get("stream"))
        with self._lock:
            self._n += 1
            n = self._n
        no = self.rifiuto(corpo)
        if no is not None:
            status, messaggio = no
            with self._lock:
                self.registro.append({**ruolo, "n": n, "model": modello, "stream": stream, "status": status,
                                      "motivo": messaggio, "max_tokens": corpo.get("max_tokens"),
                                      "reasoning": corpo.get("reasoning")})
            return httpx.Response(status, json={"error": {"code": status, "message": messaggio}},
                                  headers={"x-generation-id": "gen-finto-%d" % n})
        meta, nome_risposta = metadati(self.catalogo, modello)
        strumento = strumento_da_chiamare(corpo)
        testo = "" if strumento else self.testo_per(corpo)
        usage = self._usage(corpo, testo or json.dumps(strumento or {}), meta)
        rid = "gen-finto-%d" % n
        with self._lock:
            self.registro.append({**ruolo, "n": n, "model": modello, "stream": stream, "status": 200,
                                  "risposta_model": nome_risposta, "max_tokens": corpo.get("max_tokens"),
                                  "reasoning": corpo.get("reasoning"),
                                  "reflection_groups": gruppi_reflection(corpo),
                                  "tool": strumento["function"]["name"] if strumento else None})
        fine = "tool_calls" if strumento else "stop"
        if not stream:
            messaggio = {"role": "assistant", "content": testo}
            if strumento:
                messaggio["tool_calls"] = [strumento]
            return httpx.Response(200, json={
                "id": rid, "model": nome_risposta, "provider": "Finto", "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": fine, "message": messaggio}],
                "usage": usage}, headers={"x-generation-id": rid})
        delta = ({"role": "assistant", "tool_calls": [dict(strumento, index=0)]} if strumento
                 else {"role": "assistant", "content": testo})
        chunks = [
            {"id": rid, "model": nome_risposta, "provider": "Finto",
             "choices": [{"index": 0, "delta": delta}]},
            {"id": rid, "model": nome_risposta, "provider": "Finto",
             "choices": [{"index": 0, "delta": {}, "finish_reason": fine}]},
            {"id": rid, "model": nome_risposta, "provider": "Finto", "choices": [], "usage": usage},
        ]
        sse = "".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode("utf-8"),
                              headers={"content-type": "text/event-stream", "x-generation-id": rid})

    def trasporto(self):
        import httpx
        return httpx.MockTransport(self.gestisci)

    def rifiuti(self):
        return [r for r in self.registro if r["status"] != 200]


# Strumenti locali preferiti per il primo turno di un desk (nessuna rete: con la rete bloccata
# rispondono con un errore DICHIARATO, che il desk legge come farebbe il modello vero).
STRUMENTI_PREFERITI = ("get_portfolio_live", "search_past_memos")


def strumento_da_chiamare(corpo):
    """Il modello finto si comporta come un desk vero: al PRIMO turno con strumenti offerti ne
    chiama uno (un report senza tool sarebbe un «annuncio-senza-lavoro»), poi scrive il report."""
    strumenti = [t.get("function", {}).get("name") for t in corpo.get("tools") or [] if isinstance(t, dict)]
    scelta = corpo.get("tool_choice")
    if not strumenti or scelta in ("none", {"type": "none"}):
        return None
    forzato = (scelta.get("function") or {}).get("name") if isinstance(scelta, dict) else None
    if forzato in strumenti:   # tool_choice forzato (estrazione ACTION TABLE): si rispetta sempre
        return {"id": "call_finto_forzato", "type": "function",
                "function": {"name": forzato, "arguments": json.dumps(ARGOMENTI_FORZATI.get(forzato, {}))}}
    if any(isinstance(m, dict) and m.get("role") == "tool" for m in corpo.get("messages") or []):
        return None
    nome = next((s for s in STRUMENTI_PREFERITI if s in strumenti), strumenti[0])
    return {"id": "call_finto_1", "type": "function", "function": {"name": nome, "arguments": "{}"}}


# ACTION TABLE del testo sintetico: una sola riga HOLD su un titolo del book inventato.
ACTION_TABLE = ("\n\n## ACTION TABLE\n\n| Action | Ticker | Size | Timing | Confidence |\n"
                "|---|---|---|---|---|\n| HOLD | ZZSMOKE.MI | 0 EUR | n.d. | MEDIA |\n\n")
ARGOMENTI_FORZATI = {"emit_action_table": {"table_found": True, "rows": [
    {"action": "HOLD", "ticker": "ZZSMOKE.MI", "size_raw": "0 EUR", "timing": "n.d.", "confidence": "MEDIA"}]}}


def gruppi_reflection(corpo):
    """Riconosce il payload attestato della reflection, mai lo slug (condivisibile col Capo)."""
    for messaggio in corpo.get("messages") or []:
        if messaggio.get("role") != "user":
            continue
        try:
            dati = json.loads(messaggio.get("content"))
        except (TypeError, ValueError):
            continue
        if isinstance(dati, dict) and set(dati) == {"eligible_groups"}:
            return dati["eligible_groups"]
    return None


def testo_sintetico(corpo):
    """Testo di risposta: sopra la soglia dell'annuncio-senza-lavoro dei desk (800 caratteri) e
    del memo collassato del Capo (2000)."""
    filo = json.dumps(corpo.get("messages"), ensure_ascii=False)
    if '"ping"' in filo:
        return "pong"
    gruppi = gruppi_reflection(corpo)
    if gruppi is not None:
        assert "overall" in gruppi, "reflection smoke senza gruppo overall attestato"
        return json.dumps({"rows": [{"group_ids": ["overall"], "instruction":
            "Valutare il track record complessivo con cautela: il campione minimo non e' una "
            "garanzia di affidabilita [src: scorekeeper]."}]})
    paragrafo = ("Analisi sintetica di prova per ZZSMOKE.MI e QQSYN.MI (ticker inventati). Fonti "
                 "ufficiali non disponibili in questa prova: limite dichiarato. Consenso n.d.; nessun "
                 "fair value dichiarato. Scenario base: disciplina operativa; scenario ribassista: "
                 "pressione sui margini; scenario rialzista: domanda in ripresa. Rischio principale: "
                 "liquidita' del titolo. Nessuna proposta d'acquisto: RESEARCH. Prezzo di riferimento "
                 "inventato 123.45 EUR. ")
    return paragrafo * 8 + ACTION_TABLE + "Fine del testo sintetico."


class AdattatoreModels:
    """Adattatore `requests` per GET /models: serve la fixture come farebbe la Models API."""

    def __init__(self, doc):
        self.doc = doc
        self.letture = 0

    def send(self, request, **kwargs):
        import requests
        from requests.structures import CaseInsensitiveDict
        risposta = requests.Response()
        risposta.url = request.url
        risposta.request = request
        if request.method == "GET" and str(request.url).rstrip("/") == URL_MODELS:
            self.letture += 1
            risposta.status_code = 200
            risposta._content = json.dumps({"data": self.doc["data"]}).encode("utf-8")
        else:
            risposta.status_code = 404
            risposta._content = b'{"error": "endpoint non simulato"}'
        risposta.headers = CaseInsensitiveDict({"content-type": "application/json"})
        risposta.encoding = "utf-8"
        return risposta

    def close(self):
        pass


def monta_requests_models(adattatore, setattr_fn):
    """Instrada SOLO https://openrouter.ai/... verso l'adattatore; il resto resta sul socket
    (che nella prova e' bloccato)."""
    import requests.sessions as _sessions
    originale = _sessions.Session.get_adapter

    def get_adapter(self, url, _orig=originale):
        if str(url).startswith("https://openrouter.ai/"):
            return adattatore
        return _orig(self, url)

    setattr_fn(_sessions.Session, "get_adapter", get_adapter)


def monta_trasporto(llm_client, fornitore, setattr_fn):
    """Ogni OpenRouterClient costruito dal codice (sonda, desk, red team, Capo...) riceve il
    trasporto finto: e' il punto dove il client vero aprirebbe la connessione."""
    import httpx

    def http(timeout, trasporto, _t=fornitore.trasporto()):
        return httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0), transport=trasporto or _t)

    def http_async(timeout, trasporto, _f=fornitore):
        return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0),
                                 transport=trasporto or httpx.MockTransport(_f.gestisci))

    setattr_fn(llm_client, "_nuovo_client_http", http)
    setattr_fn(llm_client, "_nuovo_client_http_async", http_async)


# ============================================================================ runner (processo isolato)

_LOCALI = ("127.0.0.1", "::1", "localhost")


def _norm(p):
    try:
        return os.path.normcase(os.path.realpath(os.fspath(p)))
    except Exception:
        return os.path.normcase(str(p))


def _installa_guardie(radice, data_veri, violazioni):
    """Audit hook: rete non locale vietata; DB/file nel data/ vero vietati; scritture nell'albero
    vietate. Una violazione SOLLEVA (il codice puo' inghiottirla) ed e' registrata: il test la legge."""
    radice_n = _norm(radice)
    data_n = [_norm(d) for d in data_veri]

    def sotto(path, base):
        return path == base or path.startswith(base.rstrip("\\/") + os.sep)

    def hook(evento, args):
        if evento == "socket.connect":
            indirizzo = args[1]
            host = indirizzo[0] if isinstance(indirizzo, tuple) else indirizzo
            if str(host) not in _LOCALI and not str(host).startswith("127."):
                violazioni.append("rete:" + str(host))
                raise ConnectionError("rete vera nella prova smoke: " + str(host))
        elif evento == "socket.getaddrinfo":
            host = args[0]
            if host not in (None, "") and str(host) not in _LOCALI and not str(host).startswith("127."):
                violazioni.append("dns:" + str(host))
                raise ConnectionError("rete vera nella prova smoke: " + str(host))
        elif evento in ("open", "sqlite3.connect", "os.rename", "os.replace", "os.remove", "os.mkdir"):
            if not args or not isinstance(args[0], (str, bytes, os.PathLike)):
                return
            percorsi = [args[0]]
            if evento in ("os.rename", "os.replace") and len(args) > 1:
                percorsi.append(args[1])
            scrittura = evento != "open" or (len(args) > 1 and isinstance(args[1], str)
                                              and any(c in args[1] for c in "wax+"))
            if evento == "open" and len(args) > 2 and isinstance(args[2], int):
                scrittura = scrittura or bool(args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
            for p in percorsi:
                if isinstance(p, bytes):
                    p = p.decode(errors="replace")
                if str(p).startswith("file:") or str(p) == ":memory:":
                    continue
                pn = _norm(p)
                if any(sotto(pn, d) for d in data_n):
                    violazioni.append(evento + ":" + pn)
                    raise PermissionError("data/ vero toccato dalla prova smoke: " + pn)
                if scrittura and sotto(pn, radice_n):
                    violazioni.append(evento + ":" + pn)
                    raise PermissionError("scrittura nell'albero dalla prova smoke: " + pn)

    sys.addaudithook(hook)


def _finto(nome, **attr):
    """Sostituisce funzioni di DATI sul modulo VERO (import reale, altri nomi intatti)."""
    import importlib
    m = importlib.import_module(nome)
    for k, v in attr.items():
        if not hasattr(m, k):
            raise AttributeError(nome + "." + k + " non esiste piu': aggiornare la prova smoke")
        setattr(m, k, v)
    return m


class FermoDopoCapo(Exception):
    """Il Capo ha completato (memo validato dal ciclo di vita): la prova si ferma qui."""


def esegui_run(config):
    """Run settimanale vera. `config`: radice, tmp, fixture, uscita; facoltativi: fino ("capo" = ferma
    dopo il checkpoint del Capo, "pdf" = fino alla consegna), send_email, ripresa_memo_id,
    red_cap_vecchio (simula il codice delle 21:00: niente taglio al tetto del provider)."""
    radice = Path(config["radice"])
    tmp = Path(config["tmp"])
    violazioni = []
    data_veri = [radice / "data"] + [p for p in os.environ.get(
        "BELLOMBERG_SIGILLO_PROTETTE", "").split(os.pathsep) if p]
    _installa_guardie(radice, data_veri, violazioni)
    # .env del PM mai letto: la configurazione e' QUELLA passata dal test (env del processo).
    import dotenv
    dotenv.load_dotenv = lambda *a, **k: False
    for nome in ("yfinance",):
        try:
            m = __import__(nome)
            for f in ("Ticker", "Tickers", "download", "Search"):
                if hasattr(m, f):
                    setattr(m, f, lambda *a, _f=f, **k: (_ for _ in ()).throw(
                        ConnectionError("yfinance." + _f + " vietato nella prova smoke")))
        except ImportError:
            pass
    try:
        import curl_cffi.requests as _cc
        _cc.Session.request = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("curl_cffi vietato"))
    except Exception:
        pass

    catalogo, doc = carica_catalogo(config.get("fixture") or FIXTURE)
    fornitore = FornitoreFinto(catalogo)
    adattatore = AdattatoreModels(doc)
    monta_requests_models(adattatore, setattr)

    from bellomberg.core import llm_client
    monta_trasporto(llm_client, fornitore, setattr)
    from bellomberg.core import paths
    assert _norm(paths.DATA_DIR).startswith(_norm(tmp)), paths.DATA_DIR

    # ---- tool di DATI finti (dichiarati in TOOL_DATI_FINTI)
    from bellomberg.storage import memory_db
    memory_db.MemoryDB._init_chroma = lambda self: self.__dict__.update(
        chroma_client=None, col_memos=None, col_decisions=None, col_feedback=None)
    from bellomberg.agents import consigliere_multi as cm
    from bellomberg.agents import agent_tools, scorekeeper

    def _archivio_assente():
        raise FileNotFoundError("archivio filing assente nella prova smoke (dichiarato)")
    cm._filing_service = _archivio_assente
    cm.tool_get_macro_dashboard = lambda: {"indicators": {}}
    cm._try_correlation_matrix = lambda positions: None
    agent_tools.tool_get_polymarket_events = lambda q, max_results=10: {
        "results": [], "count": 0, "fetch_warnings": ["prova smoke: rete spenta"]}
    agent_tools.tool_get_hyperliquid_intel = lambda: {}
    # Funzioni sostituite SUL modulo vero (gli altri nomi restano quelli di produzione).
    _finto("bellomberg.portfolio.portfolio_analytics",
           compute_var_contribution=lambda *a, **k: {"error": "prova smoke"},
           compute_nav_history=lambda *a, **k: {"error": "prova smoke"})
    _finto("bellomberg.portfolio.advanced_metrics",
           portfolio_metrics=lambda *a, **k: {"error": "prova smoke"}, reconcile_betas=lambda *a, **k: {})
    _finto("bellomberg.market_data.news_aggregator", get_feed=lambda *a, **k: [])
    _finto("bellomberg.portfolio.twr_engine", build_recon_note=lambda nav, snaps: None,
           _load_snapshots=lambda *a, **k: [])
    _finto("bellomberg.core.freshness", check_and_update=lambda cur, *a, **k: {"checked": 0, "stale": []},
           format_for_capo=lambda *a, **k: "", format_for_memo=lambda *a, **k: "")
    # Scorekeeper sintetico: 36 esiti attestati attivano la policy reale; gli scenari negativi
    # provano che la soglia e i dettagli non sono aggirabili dal provider finto.
    scenario = config.get("reflection_scenario", "qualificata")
    if scenario not in ("qualificata", "sotto_soglia", "dettagli_assenti"):
        raise ValueError("scenario reflection smoke sconosciuto: " + str(scenario))
    n = 35 if scenario == "sotto_soglia" else 36
    dettagli = [{"id": i + 1, "memo_id": 700 + i, "action": "BUY", "hit": i < 24,
                 "confidence_bucket": "ALTA", "specialists": ["quant"], "horizon_used": "4w"}
                for i in range(n)]
    _scheda = {"computed_at": "2026-10-05T00:00:00",
               "overall": {"n": n, "hits": 24, "hit_rate_pct": round(24 / n * 100, 1), "avg_edge_pct": 1.23},
               "details": dettagli,
               "by_action": {}, "by_confidence": {}, "by_specialist": {}, "n_unmeasurable": 0,
               "n_directional_candidates": n, "n_fetch_fail": 0, "degraded": False}
    if scenario == "dettagli_assenti":
        _scheda.pop("details")
    scorekeeper.compute_scorecard = lambda *a, **k: dict(_scheda)
    _finto("bellomberg.portfolio.portfolio_risk", compute_portfolio_risk=lambda *a, **k: {"error": "prova smoke"})
    _finto("bellomberg.portfolio.portfolio_montecarlo", run_monte_carlo=lambda *a, **k: {"error": "prova smoke"})
    _finto("bellomberg.portfolio.sizing_engine", compute_sizing=lambda *a, **k: {"error": "prova smoke"})
    from bellomberg.core import current_facts
    for blocco in ("current_facts_block", "favorites_block", "pm_theses_block", "research_block"):
        if hasattr(current_facts, blocco):
            setattr(current_facts, blocco, lambda *a, _b=blocco, **k: "\n\n[CONTESTO prova smoke: " + _b + " finto]")

    # ---- DB del book sintetico e mandato, nella cartella temporanea (una ripresa li ritrova li')
    ripresa = config.get("ripresa_memo_id")
    db = memory_db.MemoryDB()
    if ripresa is None:
        with db._conn() as conn:
            for ticker in ("ZZSMOKE.MI", "QQSYN.MI"):
                conn.execute("INSERT INTO positions(ticker,nome,quantita,prezzo_medio,valuta) VALUES(?,?,?,?,?)",
                             (ticker, "Emittente inventato " + ticker, 3, 123.45, "EUR"))
        from bellomberg.core import mandato_pm as mp
        import copy
        mandato = copy.deepcopy(mp.profilo_esempio())
        mandato["versione"] = mp.VERSIONE_SCHEMA
        mandato["dichiarato_il"] = "2026-01-02"
        Path(mp.PERCORSO_MANDATO).write_text(json.dumps(mandato, ensure_ascii=False), encoding="utf-8")

    # ---- SMTP finto: il ramo che DECIDE se inviare e' quello vero, la consegna resta qui
    import smtplib
    inviati = []

    class _SMTPFinto:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a, **k):
            return (235, b"ok")

        def send_message(self, msg, *a, **k):
            inviati.append({"oggetto": str(msg.get("Subject")),
                            "allegati": [part.get_filename() for part in msg.walk() if part.get_filename()]})
            return {}

        def quit(self):
            return (221, b"bye")
    smtplib.SMTP_SSL = _SMTPFinto

    # ---- simulazione del codice delle 21:00 del 05/10: niente adattamento del tetto di uscita
    # (MOD-CAP 06/10: il taglio vive nel punto unico del client) e niente preventivo anticipato.
    if config.get("red_cap_vecchio"):
        from bellomberg.agents import red_team as _rt
        llm_client._corpo_al_tetto = lambda body, scope, trasporto_di_prova: body
        if hasattr(_rt, "_preventivo_prima_dell_invio"):   # controllo anticipato: il codice delle 21:00 non l'aveva
            _rt._preventivo_prima_dell_invio = lambda model, max_tokens, **_k: max_tokens
    # ---- fermo (facoltativo) subito DOPO il checkpoint del Capo (memo validato da validate_memo)
    fino = config.get("fino", "capo")
    from bellomberg.storage import weekly_run_store as wrs
    _init = wrs.WeeklyRunStore.__init__
    _complete = wrs.WeeklyRunStore.complete
    catturato = {}

    def init(self, *a, **k):
        _init(self, *a, **k)
        catturato["store"] = self   # anche se la run muore prima del Capo

    def complete(self, name, payload, *a, **k):
        risultato = _complete(self, name, payload, *a, **k)
        if name == "capo":
            catturato["memo"] = payload.get("memo")
            catturato["usage"] = payload.get("usage")
            if fino == "capo":
                raise FermoDopoCapo()
        return risultato
    wrs.WeeklyRunStore.__init__ = init
    wrs.WeeklyRunStore.complete = complete

    esito = {"arrivato_al_capo": False, "eccezione": None, "fino": fino, "ripresa_memo_id": ripresa}
    t0 = time.perf_counter()
    try:
        if ripresa is None:
            risultato = cm.run_multi_agent(send_email=bool(config.get("send_email")))
        else:
            risultato = cm.run_multi_agent(resume_memo_id=int(ripresa), authorize_new_ai=True,
                                           send_email=bool(config.get("send_email")))
        esito["risultato"] = {k: v for k, v in (risultato or {}).items()
                              if k in ("status", "analytical_status", "delivery_status", "artifacts",
                                       "phase", "first_error", "last_error", "memo_id")}
        esito["arrivato_al_capo"] = catturato.get("memo") is not None or (risultato or {}).get(
            "analytical_status") == "complete"
    except FermoDopoCapo:
        esito["arrivato_al_capo"] = True
    except BaseException as exc:   # la run e' morta: la causa va al test
        esito["eccezione"] = type(exc).__name__ + ": " + str(exc)[:600]
    esito["email_inviate"] = inviati
    store_ = catturato.get("store")
    esito["memo_id"] = getattr(store_, "memo_id", None)
    if store_ is not None:
        esito["capo_checkpoint"] = store_.get("capo") is not None
        if catturato.get("memo") is None and store_.get("capo") is not None:
            catturato["memo"] = store_.get("capo").get("memo")
            catturato["usage"] = store_.get("capo").get("usage")
    esito["durata_s"] = round(time.perf_counter() - t0, 1)
    store = catturato.get("store")
    bb = getattr(store, "blackboard", None)
    esito["memo"] = catturato.get("memo")
    esito["usage_capo"] = {k: v for k, v in (catturato.get("usage") or {}).items()
                           if k in ("complete", "stop_reason", "model", "api_calls", "error")}
    if store is not None:
        try:
            journal = getattr(store, "request_journal", None)
            if journal is not None:
                costi = journal.summary()
            else:   # ripresa di sola consegna: il journal si legge senza aprirlo in scrittura
                from bellomberg.core.request_journal import RequestJournal
                costi = RequestJournal.read_summary(store.status()["request_journal_path"])
            esito["costi_journal"] = {k: v for k, v in costi.items() if k != "requests"}
            esito["stati_richieste"] = sorted({r["state"] for r in costi.get("requests") or []})
        except Exception as exc:
            esito["costi_journal"] = {"errore": type(exc).__name__ + ": " + str(exc)[:200]}
        stato = store.status()
        esito["stato_run"] = {k: stato.get(k) for k in ("status", "phase", "first_error", "last_error")}
        attesi = ([(c.name, r) for r in (0, 1) for c in cm.SPECIALIST_ORDER]
                  + [(c.name, 2) for c in cm._classes_for_round(2)])
        esito["desk_attesi"] = len(attesi)
        esito["desk_mancanti"] = ["%s:R%d" % (n, r) for n, r in attesi
                                  if store.get("desk:%s:%d" % (n, r)) is None]
        esito["red_team_completato"] = store.get("red_team") is not None
    if bb is not None:
        esito["tool_health_ko"] = list((bb.data.get("_tool_health") or {}).get("ko") or [])
        esito["red_team_gap"] = (bb.data.get("_red_team_gap") or {}).get("message")
        esito["desk_gaps"] = {k: (v or {}).get("message") for k, v in (bb.data.get("_desk_gaps") or {}).items()}
        esito["red_team"] = str((bb.data.get("_red_team") or {}).get(1) or "")[:300]
        esito["checkpoint_red_team"] = {
            str(k): {c: (v or {}).get(c) for c in ("max_tokens", "iteration", "status", "calls")}
            for k, v in (getattr(bb, "specialist_checkpoints", None) or {}).items() if "red" in str(k).lower()}
    esito["fornitore"] = fornitore.registro
    esito["letture_models"] = adattatore.letture
    esito["violazioni"] = violazioni
    Path(config["uscita"]).write_text(json.dumps(esito, ensure_ascii=False, default=str, indent=1),
                                      encoding="utf-8")
    return esito


if __name__ == "__main__":
    _config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    # src dell'albero di QUESTO file in testa: la run vera e' quella dell'albero sotto prova
    sys.path.insert(0, str(Path(_config["radice"]) / "src"))
    esegui_run(_config)
