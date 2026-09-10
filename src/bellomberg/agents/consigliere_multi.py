"""
BELLOMBERG - Personal AI Hedge Fund Terminal
Multi-agent consigliere v3 - with persistent memory (Phase 1 complete)

Pipeline:
1. PRIMING: portfolio (SQLite live) + macro + correlation matrix
2. INIT memo in DB (per riferimento dei specialisti)
3. Ripipeline (15/07): R0 recon su Sonnet (6 specialisti, parallelo) -> R1 analisi
   Opus a ONDATE di pipeline (macro+eventdesk -> crypto+fundamentals -> quant ->
   options: la validazione avviene nello stesso round) -> red team -> R2 SELETTIVO
   sequenziale (fundamentals -> quant -> options). 21 -> 15 agent-round.
4. CAPO synthesis (con full memory context)
5. Save memo + decision extraction (auto-popola tabella decisions)
6. PDF MEMO + PDF APPENDICE QUANT + DCF Excel (se generati)
7. EMAIL UNICA con allegati
"""
import os
import sys
import json
import glob
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

from bellomberg.agents.bellomberg import print_banner, BRAND_NAME, VERSION
from bellomberg.agents.specialists import (
    Blackboard,
    MacroSpecialist, OptionsSpecialist, EventDeskSpecialist,
    FundamentalsSpecialist, CryptoSpecialist, QuantSpecialist,
)
from bellomberg.agents.capo import run_capo
from bellomberg.reporting.email_sender import email_configurata
from bellomberg.agents.agent_tools import tool_get_macro_dashboard, tool_quant_compute
from bellomberg.core.paths import MODELS_DIR, REPORT_DIR
from bellomberg.storage.memory_db import MemoryDB, RESEARCH_NOTES_DIR


# Ripipeline 15/07 (dossier 03, ok PM esplicito): ORDINE DI PIPELINE — chi valida
# viene DOPO chi propone, cosi' la validazione avviene nello stesso round R1
# (prima Quant era PRIMA di Fundamentals: il 3° round completo esisteva solo per quello).
SPECIALIST_ORDER = [
    MacroSpecialist,          # regime e temi: il terreno di gioco
    EventDeskSpecialist,      # fusione News+Politics 15/07: eventi, catalyst, probabilita'
    CryptoSpecialist,
    FundamentalsSpecialist,   # propone i nomi (usa macro/eventi gia' in draft R1)
    QuantSpecialist,          # valida i numeri dei candidati NELLO STESSO round
    OptionsSpecialist,        # ondata DOPO Quant: strutture SOLO sui candidati validati
]

# R1 a ONDATE di pipeline: dentro l'ondata in parallelo, BARRIERA tra ondate
# (chi valida VEDE i draft R1 di chi propone). Quant e Options in ondate SEPARATE
# (review 15/07): Options struttura sui candidati GIA' validati da Quant — in
# parallelo avrebbe strutturato anche i bocciati. R2 SELETTIVO: replicano al red
# team solo i desk che decidono numeri e strutture; macro/eventdesk/crypto chiudono
# in R1 (chiude anche la voce P2 "Crypto declassato a R0+R1"). Run: 21 -> 15 round.
R1_STAGES = [["macro", "eventdesk"], ["crypto", "fundamentals"], ["quant"], ["options"]]
R2_SPECIALISTS = {"fundamentals", "quant", "options"}


def _classes_for_round(round_n):
    """Chi gira in questo round (R2 = solo il sottoinsieme selettivo)."""
    if round_n == 2:
        return [c for c in SPECIALIST_ORDER if c.name in R2_SPECIALISTS]
    return list(SPECIALIST_ORDER)


def _r1_pipeline_stages(classes):
    """Piano a ondate per R1. Difensivo: uno specialista fuori da R1_STAGES
    (comitato cambiato senza aggiornare le ondate) finisce in un'ondata finale
    DICHIARATA nel log, mai perso in silenzio."""
    name2cls = {c.name: c for c in classes}
    plan, covered = [], set()
    for stage in R1_STAGES:
        cur = [name2cls[n] for n in stage if n in name2cls]
        covered.update(c.name for c in cur)
        if cur:
            plan.append(cur)
    extra = [c for c in classes if c.name not in covered]
    if extra:
        _log("  [!] R1: specialisti fuori dalle ondate di pipeline (aggiungerli a R1_STAGES): "
             + ", ".join(c.name for c in extra))
        plan.append(extra)
    return plan


def _log(msg):
    print("[" + datetime.now().strftime("%H:%M:%S") + "] " + msg)


_MOTIVO_USCITA = {"testo": ""}   # letto dal gestore di crash del __main__: F4 vede il motivo, non «2»


def mandato_o_esci():
    """05/09 (criterio 5, spec §5): senza il MANDATO del PM il comitato NON parte — e lo dice
    PRIMA di costruire il blackboard e di pagare i desk, non al Capo dopo un'ora. Torna il
    mandato letto dal disco; assente/incompleto = messaggio con la causa e uscita 2."""
    from bellomberg.core import mandato_pm
    try:
        return mandato_pm.carica()
    except mandato_pm.MandatoMancante as e:
        _log("MANDATO NON DICHIARATO: " + str(e))
        _log("Il comitato non parte senza il mandato del PM: compila la pagina Mandato (F11) e rilancia.")
        _MOTIVO_USCITA["testo"] = "MANDATO NON DICHIARATO: " + str(e)[:160]
        raise SystemExit(2)


def _parallel_workers():
    """Fase 3 parallelizzazione: quanti specialisti insieme per round.
    Tarabile dal PM via .env (CONSIGLIERE_PARALLEL); 1 = sequenziale com'era.
    Default 4 (raccomandazione audit: 4-5; ogni agente ha il SUO prefisso di
    cache #200a, quindi il parallelismo non tocca il prompt caching)."""
    try:
        return max(1, int(os.getenv("CONSIGLIERE_PARALLEL", "4")))
    except Exception:
        return 4


def run_round(blackboard, round_n):
    _log("=" * 60)
    _log("ROUND " + str(round_n))
    _log("=" * 60)
    blackboard.current_round = round_n
    # seed della cache scorer PRIMA dei thread: cosi' i thread non mutano le
    # CHIAVI ESTERNE di blackboard.data (solo la sotto-dict, un nome ciascuno)
    blackboard.data.setdefault("_score_cache", {})

    def _run_one(SpClass):
        name = getattr(SpClass, "name", SpClass.__name__)
        try:
            sp = SpClass(blackboard)
            sp.run(round_n)
        except Exception as e:
            _log("[!] " + name + " round " + str(round_n) + " failed: " + str(e))
            try:
                blackboard.write(name, round_n, "[ERROR]: " + str(e))
            except Exception:
                pass

    classes = _classes_for_round(round_n)
    if round_n == 2:
        # R2 SELETTIVO e SEQUENZIALE in ordine di pipeline (review 15/07): Quant deve
        # vedere la revisione R2 di Fundamentals e Options il verdetto R2 di Quant —
        # in parallelo un declassamento post-red-team non arriverebbe mai a valle.
        _log("R2 SELETTIVO in pipeline: " + " -> ".join(c.name for c in classes)
             + " (macro/eventdesk/crypto chiudono in R1)")
        for SpClass in classes:
            _run_one(SpClass)
        return

    workers = _parallel_workers()
    if workers <= 1:
        # sequenziale = gia' in ordine di pipeline: chi valida vede chi propone
        for SpClass in classes:
            _run_one(SpClass)
        return
    from concurrent.futures import ThreadPoolExecutor
    if round_n == 1:
        # R1 a ONDATE di pipeline (ripipeline 15/07): dentro l'ondata in parallelo,
        # barriera tra ondate — Fundamentals vede i draft R1 di Macro/EventDesk,
        # Quant/Options vedono i candidati di Fundamentals nello stesso round.
        plan = _r1_pipeline_stages(classes)
        _log("R1 a ondate di pipeline: " + "  ->  ".join("+".join(c.name for c in stage) for stage in plan))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="spec") as ex:
            for stage in plan:
                futs = [ex.submit(_run_one, SpClass) for SpClass in stage]
                for f in futs:
                    f.result()  # barriera di ondata (_run_one non propaga)
        return
    # R0 (recon) e R2 (selettivo): nessuna dipendenza interna al round -> tutti insieme.
    # NB semantica: in parallelo ogni specialista vede i report del round PRECEDENTE;
    # ask_specialist resta disponibile e best-effort durante il round.
    _log("Specialisti in parallelo: " + str(workers) + " worker (CONSIGLIERE_PARALLEL)")
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="spec") as ex:
        futures = [ex.submit(_run_one, SpClass) for SpClass in classes]
        for f in futures:
            f.result()  # _run_one non propaga: .result() serve solo da barriera


def _record_capo_usage(blackboard, usage, duration_s):
    """Propagate the Capo's measured application-call count, including retries."""
    calls = usage.get("api_calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 1:
        raise ValueError("Capo api_calls non consegnato o non valido: nessun conteggio inventato")
    return blackboard.record_usage("capo", 3, usage.get("model"), usage,
        duration_s=duration_s, api_calls=calls, cache_ttl=None,
        status="api_error" if usage.get("error") else "ok")


def _record_side_usage(blackboard, agent, usage):
    """Registra nel conto della run le chiamate LLM 'laterali' (#44/finding 4).

    reflection (#210) e action_table_extract (#200c) fanno UNA chiamata Sonnet vera
    ciascuna, ma non passavano da record_usage: non esistevano ne' in usage_log, ne'
    in llm_usage, ne' nel totale mostrato al PM — che si presentava COMPLETO pur
    essendo strutturalmente incompleto (fallback silenzioso, regola PM 14\07).

    usage = il dict riempito via usage_out dal modulo chiamato. status "skipped" (o
    dict vuoto) = NESSUNA chiamata partita -> niente da registrare, non e' un buco.
    Best-effort: un errore del contatore non deve MAI far cadere la run.
    """
    try:
        if not isinstance(usage, dict) or not usage:
            return None
        status = usage.get("status") or "usage_unknown"
        if status == "skipped":
            return None
        model = usage.get("model")
        if not model:
            # chiamata partita ma modello ignoto: il costo NON e' calcolabile e lo si
            # dichiara (mai uno 0,00 di comodo) -> record_usage -> model_unknown.
            status = "usage_unknown" if status == "ok" else status
        entry = blackboard.record_usage(
            agent, 1, model, usage,
            duration_s=usage.get("duration_s"),
            api_calls=int(usage.get("api_calls") or 0),
            cache_ttl=None,  # nessun prompt caching in queste due chiamate
            status=status)
        # 21/07 (minore run #45): formato costi uniforme alle righe agente — prima
        # qui usciva il float grezzo (0.013723463109396698) accanto agli "0,19 EUR".
        _c = (entry or {}).get("cost_eur")
        _c_s = ("%.2f EUR" % _c).replace(".", ",") if isinstance(_c, (int, float)) else str(_c)
        _log(agent + " usage: in=" + str(usage.get("in")) + " out=" + str(usage.get("out"))
             + " status=" + str((entry or {}).get("status"))
             + " costo=" + _c_s)
        return entry
    except Exception as e:
        _log("[!] " + str(agent) + " usage non registrato (procedo): " + str(e))
        return None


def _collect_dcf_files(start_time):
    # audit/11 §5: niente early-return se manca models/ — scartava in silenzio anche i
    # report/VAL_*.xlsx dal glob sotto (glob su dir inesistente ritorna gia' [])
    dcf_files = []
    for path in (glob.glob(os.path.join(str(MODELS_DIR), "DCF_*.xlsx"))
                 + glob.glob(os.path.join(str(MODELS_DIR), "VAL_*.xlsx"))
                 + glob.glob(os.path.join(str(REPORT_DIR), "VAL_*.xlsx"))):
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if mtime >= start_time:
                dcf_files.append(path)
        except Exception:
            continue
    # C1 16/07 (cintura oltre alla sovrascrittura giornaliera di dcf_engine): UN file
    # per ticker per run — se per qualunque via ne restano due (es. base + _FLAGGED),
    # si allega solo il piu' recente. Ticker dal nome file; fuori pattern = tenuto.
    import re as _re
    # review 16/07: stamp OPZIONALE — i canonici (VAL_TICKER.xlsx) e i loro _FLAGGED
    # devono entrare nel dedup, non finire nel 'resto' senza raggruppamento.
    _pat = _re.compile(r"^(?:VAL|DCF)_(.+?)(?:_(?:\d{8}(?:_\d{4})?|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}))?(?:_FLAGGED)?\.xlsx$")
    best = {}
    resto = []
    for path in dcf_files:
        m = _pat.match(os.path.basename(path))
        if not m:
            resto.append(path)
            continue
        k = m.group(1).upper()
        if k not in best or os.path.getmtime(path) > os.path.getmtime(best[k]):
            best[k] = path
    dedup = sorted(list(best.values()) + resto)
    if len(dedup) < len(dcf_files):
        print(f"[DCF] dedup allegati: {len(dcf_files)} file -> {len(dedup)} (uno per ticker)")
    return dedup


def _try_correlation_matrix(positions):
    if not positions:
        return None
    from bellomberg.storage.negozi_privati import carica_alias
    alias = carica_alias()
    if alias["origine"] in ("assente", "illeggibile"):
        _log("  [!] Correlation matrix skipped: alias_fonti %s: %s" % (
            alias["origine"], alias["motivo"] or "motivo n.d."))
        return None
    # Proxy SOLO per la correlazione; output rietichettato coi simboli reali.
    proxy_map = alias["alias"]["correlazione"]
    top = sorted([p for p in positions if p.get("peso_pct")],
                  key=lambda x: x["peso_pct"] or 0, reverse=True)[:8]
    reali = [str(p.get("ticker") or "").strip().upper() for p in top]
    reali = [t for t in reali if t]
    if not reali:
        return None
    data_by_real = {t: proxy_map.get(t, t) for t in reali}
    real_by_proxy = {}
    for reale, proxy in data_by_real.items():
        if proxy in real_by_proxy and real_by_proxy[proxy] != reale:
            _log("  [!] Correlation matrix skipped: reverse mapping ambiguo, %s e %s "
                 "usano lo stesso proxy %s" % (real_by_proxy[proxy], reale, proxy))
            return None
        real_by_proxy[proxy] = reale
    tickers = [data_by_real[t] for t in reali]

    def _relabel(obj):
        if isinstance(obj, dict):
            return {real_by_proxy.get(k, k): _relabel(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [real_by_proxy.get(x, x) if isinstance(x, str) else _relabel(x) for x in obj]
        return obj

    try:
        result = tool_quant_compute("correlation_matrix", tickers=tickers, period="6mo")
        if result and "matrix" in result:
            used = [p for p in real_by_proxy
                    if real_by_proxy[p] != p and (p in str(result["matrix"]) or p in tickers)]
            if used:
                _log("  Correlation: dati via proxy dichiarati " +
                     ", ".join(f"{real_by_proxy[p]}<-{p}" for p in sorted(set(used) & set(real_by_proxy))))
            return _relabel(result["matrix"])
    except Exception as e:
        _log("  [!] Correlation matrix fail: " + str(e))
    return None


def _portfolio_priming_log(portfolio):
    n = portfolio.get("n_positions", 0)
    totale = portfolio.get("totale_valore_mercato_eur")
    if totale is None:
        motivo = ", ".join(portfolio.get("fx_incomplete") or []) or "causa n.d."
        return f"  Portfolio: {n} positions, EUR n.d. (FX incompleto: {motivo})"
    return f"  Portfolio: {n} positions, EUR {totale:,.0f}"


def _send_weekly_email(attachments):
    """Il booleano del mittente e' l'esito: False non e' un invio riuscito."""
    if not email_configurata():
        _log("[!] Email not configured")
        return False
    _log("Sending Bellomberg email with all attachments")
    try:
        from bellomberg.reporting.email_sender import invia_email_multi_allegati
        sent = invia_email_multi_allegati(
            pdf_paths=attachments,
            oggetto="[BELLOMBERG] Weekly Research - " + datetime.now().strftime("%d/%m/%Y"),
        )
        if sent is True:
            _log("Email sent (" + str(len(attachments)) + " files)")
            return True
        _log("[!] Email NON inviata: mittente ha restituito esito negativo (vedi log SMTP/allegati)")
    except Exception as e:
        _log("[!] Email error: " + str(e))
    return False


def run_multi_agent():
    start_time = datetime.now()
    print_banner()
    mandato_o_esci()   # 05/09 (criterio 5): niente mandato = niente run, dichiarato subito
    # log dai valori VERI (doc-fix audit/21 App. C: diceva "All agents on
    # claude-opus-4-8" ma R0 e' Sonnet dal 15/07 — il log ora non puo' mentire)
    try:
        # 05/09 (ordine PM): i modelli vivono nel .env, uno per desk; qui la base R1/R2,
        # R0 e Capo, con «n.d. (VARIABILE assente)» al posto di un nome inventato.
        from bellomberg.core.llm_client import modello_o_buco as _mob
        _log(f"Starting weekly run | R1/R2 {_mob('consigliere')} | R0 {_mob('consigliere', round_n=0)}"
             f" | Capo {_mob('capo')} | memory-aware")
    except Exception:
        _log("Starting weekly run | memory-aware (model strings non importabili)")

    # MEMORY DB
    try:
        db = MemoryDB()
        _log("MemoryDB ready: " + db.db_path)
    except Exception as e:
        _log("[!] MemoryDB init failed: " + str(e))
        db = None

    # 16/07 (richiesta PM): auto-archivio delle proposte operative PENDING >7 giorni
    # (la RESEARCH resta aperta finche' il PM non la archivia o la run la promuove).
    if db:
        try:
            _n_exp = db.auto_expire_stale_decisions(days=7)
            if _n_exp:
                _log("Decisions: %d proposte PENDING >7g auto-archiviate (EXPIRED, restano nel DB)" % _n_exp)
        except Exception as e:
            _log("  [!] auto-expire decisions failed (proseguo): " + str(e))

    # PRIMING - portfolio da DB (fallback Excel se vuoto)
    _log("Priming portfolio + macro")
    portfolio = None
    if db:
        try:
            portfolio = db.get_portfolio_summary()
            if portfolio.get("n_positions", 0) == 0:
                # 202-A2: niente fallback Excel (#194): se il DB e' vuoto e' un errore da fermare
                _log("  [!] DB portfolio VUOTO: controlla data/consigliere.db prima di lanciare la run")
        except Exception as e:
            _log("  [!] DB portfolio read failed: " + str(e))

    if portfolio and portfolio.get("n_positions"):
        _log(_portfolio_priming_log(portfolio))

    try:
        macro = tool_get_macro_dashboard()
        _log("  Macro: " + str(len(macro.get("indicators", {}))) + " indicators")
    except Exception:
        macro = None

    correlation_matrix = None
    try:
        if portfolio and portfolio.get("positions"):
            correlation_matrix = _try_correlation_matrix(portfolio["positions"])
            if correlation_matrix:
                _log("  Correlation matrix: " + str(len(correlation_matrix)) + " tickers")
    except Exception:
        pass

    # INIT memo placeholder in DB (riempito al termine)
    memo_id = None
    if db:
        try:
            memo_id = db.save_memo(
                full_markdown="[IN PROGRESS]",
                portfolio_nav_eur=portfolio.get("totale_valore_mercato_eur") if portfolio else None,
                title="Bellomberg Weekly - " + datetime.now().strftime("%d/%m/%Y"),
            )
            _log("Memo placeholder #" + str(memo_id) + " created in DB")
        except Exception as e:
            _log("[!] Memo init failed: " + str(e))

    # BLACKBOARD con DB + memo_id
    bb = Blackboard(memory_db=db, memo_id=memo_id)
    # totale report atteso (ripipeline: R0+R1 tutti, R2 selettivo) -> heartbeat/UI
    bb.expected_reports = len(SPECIALIST_ORDER) * 2 + len(_classes_for_round(2))
    # chi replica in R2: per gli ALTRI il report R1 e' il finale e va persistito
    # come tale (v. Blackboard.write) — senza questo la memoria li perderebbe
    bb.r2_specialists = set(R2_SPECIALISTS)

    # HEALTH-CHECK PRE-RUN (audit/07 §3, P1): il giorno del memo #42 var_contribution
    # rispondeva "insufficient history: 0 obs" e la run e' partita comunque, senza
    # decomposizione del rischio e senza che nessuno lo sapesse. Ora i tool chiave
    # vengono pingati PRIMA dei round; i KO finiscono nel log E nel prompt del Capo
    # (stesso pattern del guardrail beta / guardie Polymarket).
    tool_health = {"ok": [], "ko": []}

    def _probe(name, fn):
        try:
            r = fn()
            if isinstance(r, dict) and r.get("error"):
                tool_health["ko"].append(name + " -> " + str(r["error"])[:160])
            elif r is None or (hasattr(r, "__len__") and len(r) == 0):
                tool_health["ko"].append(name + " -> risposta vuota")
            else:
                tool_health["ok"].append(name)
            return r
        except Exception as e:
            tool_health["ko"].append(name + " -> " + type(e).__name__ + ": " + str(e)[:160])
            return None

    _log("HEALTH-CHECK pre-run dei tool del comitato")
    if portfolio and portfolio.get("n_positions"):
        tool_health["ok"].append("portfolio (" + str(portfolio["n_positions"]) + " posizioni)")
    else:
        tool_health["ko"].append("portfolio -> DB vuoto o illeggibile")
    if macro and macro.get("indicators"):
        tool_health["ok"].append("macro_dashboard (" + str(len(macro["indicators"])) + " indicatori)")
    else:
        tool_health["ko"].append("macro_dashboard -> vuoto/KO")
    try:
        from bellomberg.portfolio.portfolio_analytics import compute_var_contribution
        _probe("var_contribution", lambda: compute_var_contribution())
    except Exception as e:
        tool_health["ko"].append("var_contribution -> import: " + str(e)[:120])
    try:
        from bellomberg.portfolio.advanced_metrics import portfolio_metrics
        _probe("portfolio_metrics", lambda: portfolio_metrics())
    except Exception as e:
        tool_health["ko"].append("portfolio_metrics -> import: " + str(e)[:120])
    try:
        from bellomberg.market_data.news_aggregator import get_feed
        _probe("news_feed", lambda: get_feed(limit=5))
    except Exception as e:
        tool_health["ko"].append("news_feed -> import: " + str(e)[:120])
    # F5 (riallineamento 23/07, audit/20): riconciliazione NAV come allarme di
    # PRIMA CLASSE — una run che parte con NAV live lontano dallo snapshot
    # ufficiale lo DICHIARA al Capo (stesso canale dei tool KO), mai zitta.
    try:
        from bellomberg.portfolio.twr_engine import build_recon_note, _load_snapshots, RECON_TOLERANCE_PCT
        _snaps = _load_snapshots()
        _nav_live = float((portfolio or {}).get("nav_total_eur") or 0)
        _rec = build_recon_note(_nav_live, _snaps)
        if _rec is None:
            tool_health["ko"].append(
                "riconciliazione_nav -> n.d. (nessuno snapshot ufficiale in nav_snapshots)")
        elif _rec.get("breach"):
            tool_health["ko"].append(
                "riconciliazione_nav -> FUORI TOLLERANZA: NAV live "
                f"{_rec['nav_live_eur']:.0f} EUR vs snapshot {_rec['last_snapshot_date']} "
                f"{_rec['last_snapshot_nav_eur']:.0f} EUR (delta {_rec['delta_pct']:+.2f}%, "
                f"soglia {RECON_TOLERANCE_PCT}%) — dichiarare nel memo, non usare il NAV come certo")
        elif _rec.get("delta_pct") is None:
            tool_health["ko"].append(
                "riconciliazione_nav -> delta non calcolabile (snapshot NAV a 0)")
        else:
            tool_health["ok"].append(
                "riconciliazione_nav (delta " + f"{_rec['delta_pct']:+.2f}%" + ")")
    except Exception as e:
        tool_health["ko"].append("riconciliazione_nav -> " + type(e).__name__ + ": " + str(e)[:120])
    for _l in tool_health["ok"]:
        _log("  [OK] " + _l)
    for _l in tool_health["ko"]:
        _log("  [KO] " + _l)
    bb.data["_tool_health"] = tool_health

    # FRESHNESS CHECK (audit/07 §2-3, P1 + regola PM "mai fallback, precisione"):
    # confronta i dati esterni con lo snapshot della run precedente e marca STALE
    # i valori fermi (funding +10,95% identico per 4 memo) o con osservazione
    # vecchia (il "DXY in risalita" del #42 era un FRED di 11 giorni prima).
    freshness_report = None
    try:
        from bellomberg.core.freshness import check_and_update
        _cur = {}
        for _k, _ind in ((macro or {}).get("indicators") or {}).items():
            if isinstance(_ind, dict) and _ind.get("value") is not None:
                # prefisso = fonte dichiarata (P1 14/07: le serie native hanno
                # 'src' ONS/Eurostat/IMF/BCB; quelle FRED restano 'fred:')
                _src = str(_ind.get("src") or "fred").lower()
                _cur[_src + ":" + _k] = {"value": _ind.get("value"), "obs_date": _ind.get("date")}
        try:
            from bellomberg.agents.agent_tools import tool_get_hyperliquid_intel
            _hl = tool_get_hyperliquid_intel()
            for _row in (_hl.get("top_10_perps_by_oi") or []):
                if _row.get("asset") in ("BTC", "ETH", "SOL", "HYPE") \
                        and _row.get("funding_annualized_pct") is not None:
                    _cur["hl_funding:" + _row["asset"]] = {
                        "value": _row["funding_annualized_pct"], "obs_date": None}
        except Exception as _he:
            _log("  [!] freshness: hyperliquid non raggiungibile (" + str(_he)[:80] + ")")
        if _cur:
            freshness_report = check_and_update(_cur)
            _log("Freshness: " + str(freshness_report["checked"]) + " dati esterni, "
                 + str(len(freshness_report["stale"])) + " STALE")
            for _s in freshness_report["stale"]:
                _log("  [STALE] " + _s)
            bb.data["_freshness"] = freshness_report
    except Exception as e:
        _log("[!] freshness check skipped: " + str(e))

    # SCOREKEEPER #190/#211 (Fase 2 loop che apprende): ricalcolo PRE-RUN in
    # puro codice dell'esito di mercato delle call passate; il blocco TRACK
    # RECORD entra nella memoria del Capo e degli specialisti via memory_db.
    # Guarded: se fallisce, le memorie usano lo snapshot precedente (o niente).
    _progress_scorecard = None
    _progress_score_error = None
    try:
        from bellomberg.agents.scorekeeper import compute_scorecard
        _sc = compute_scorecard(db=db, force=True)
        _progress_scorecard = _sc
        bb.data["_scorekeeper"] = {k: _sc[k] for k in
                                    ("computed_at", "overall", "by_action",
                                     "by_confidence", "by_specialist", "n_unmeasurable",
                                     "n_directional_candidates", "n_fetch_fail", "degraded")}
        _ov = _sc.get("overall") or {}
        _log("Scorekeeper #190: " + str(_ov.get("n", 0)) + " call misurate, hit-rate "
             + str(_ov.get("hit_rate_pct")) + "%, edge medio "
             + str(_ov.get("avg_edge_pct")) + "% (" + str(_sc.get("n_unmeasurable", 0))
             + " non misurabili)")
    except Exception as e:
        _progress_score_error = type(e).__name__ + ": " + str(e)
        _log("[!] scorekeeper skipped: " + str(e))

    # ROUND 0 (recon) + ROUND 1 (draft)
    for r in [0, 1]:
        run_round(bb, r)

    # RED TEAM (#181): TRA R1 e R2 (voce P1 "tardivo": prima girava DOPO l'R2 e
    # gli specialisti non potevano mai replicare). Attacca i draft R1; la critica
    # entra nella blackboard di R2 (whitelist) + nel DB via Blackboard.write.
    try:
        from bellomberg.agents.red_team import run_red_team
        _log("=" * 60)
        _log("RED TEAM (devil's advocate) challenge sui draft R1")
        crit = run_red_team(bb, portfolio_data=portfolio, memory_db=db)
        if crit:
            _log("Red team critica: " + str(len(crit)) + " chars (visibile in R2)")
    except Exception as e:
        _log("[!] Red team skipped: " + str(e))

    # ROUND 2 (cross-review + replica al red team)
    run_round(bb, 2)

    # MOTORE DI SIZING (#184): rischio + limiti deterministici vol x correlazione PRIMA del Capo
    risk_data = None
    sizing_context = None
    try:
        from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
        risk_data = compute_portfolio_risk()
        if isinstance(risk_data, dict) and risk_data.get("error"):
            _log("  [!] risk metrics: " + str(risk_data.get("error"))); risk_data = None
    except Exception as e:
        _log("[!] risk metrics skipped: " + str(e))
    # REPLAY STRESS GFC per il budget #187 (best-effort: se manca, il buco e'
    # dichiarato dal sizing e il vincolo NON viene applicato — mai numeri di ripiego)
    stress_data = None
    try:
        from bellomberg.portfolio.portfolio_montecarlo import run_monte_carlo
        stress_data = run_monte_carlo(horizon_days=252, n_sims=1000, method="block_bootstrap",
                                      stress_scenario="gfc_2008", seed=7)
        if isinstance(stress_data, dict) and stress_data.get("error"):
            _log("  [!] replay GFC per budget: " + str(stress_data["error"])); stress_data = None
        elif stress_data is not None:
            _sm = stress_data.get("stress_meta") or {}
            _log("Replay GFC per budget #187: window_loss "
                 + str(_sm.get("window_loss_pct")) + "% ("
                 + str(len(_sm.get("proxied") or {})) + " proxy dichiarati)"
                 + (" [FALLBACK: replay non disponibile]" if stress_data.get("stress_fallback") else ""))
    except Exception as e:
        _log("[!] replay GFC per budget skipped: " + str(e))
    try:
        from bellomberg.portfolio.sizing_engine import compute_sizing, format_for_capo
        _sz = compute_sizing(portfolio, risk_data, stress_data=stress_data)
        if _sz and not _sz.get("error"):
            sizing_context = format_for_capo(_sz)
            bb.data["_sizing"] = _sz
            _log("Sizing engine: " + str(_sz["summary"]["n_positions"]) + " nomi, dispiegabile EUR "
                 + "{:,.0f}".format(_sz["summary"]["deployable_from_cash_eur"]))
            _bud = (_sz["summary"].get("stress_var_budget") or {})
            if _bud.get("binding"):
                _log("  [BUDGET #187] VINCOLA: capacita' ridotta a EUR "
                     + "{:,.0f}".format(_bud.get("additional_capacity_eur") or 0))
    except Exception as e:
        _log("[!] Sizing engine skipped: " + str(e))

    # GUARDRAIL BETA (13/07): verdetto di riconciliazione dei 3 motori nel contesto
    # del Capo — nel memo #42 un beta artefatto (0,04) ha deciso da solo il "no hedge".
    try:
        from bellomberg.portfolio.advanced_metrics import reconcile_betas
        _rb = reconcile_betas()
        if isinstance(_rb, dict) and _rb.get("verdict"):
            bb.data["_beta_reconcile"] = _rb
            _line = ("\n\n=== GUARDRAIL BETA (riconciliazione 3 motori) ===\n"
                     "verdetto: " + str(_rb["verdict"])
                     + " | beta: " + str(_rb.get("betas"))
                     + (" | consenso: " + str(_rb["beta_consensus"])
                        if _rb.get("beta_consensus") is not None else "")
                     + "\nREGOLA: se il verdetto e' UNRELIABLE, il beta NON e' un argomento "
                       "decisionale valido (vietati verdetti di hedge basati sul beta) "
                       "finche' non riconciliato.")
            sizing_context = (sizing_context or "") + _line
            _log("Guardrail beta: " + str(_rb["verdict"]) + " " + str(_rb.get("betas")))
    except Exception as e:
        _log("[!] guardrail beta skipped: " + str(e))

    # CRUSCOTTO SCORING (#186b): sintesi degli score deterministici per il Capo + memo
    scoring_context = None
    try:
        from bellomberg.agents.specialist_scores import format_scoreboard, collect_scoreboard
        scoring_context = format_scoreboard(bb.data.get("_score_cache"))
        _nsc = len(collect_scoreboard(bb.data.get("_score_cache")))
        if scoring_context:
            _log("Scoreboard: " + str(_nsc) + " score deterministici raccolti per il memo")
    except Exception as e:
        _log("[!] Scoreboard skipped: " + str(e))

    # HEALTH-CHECK -> prompt del Capo: i tool KO vanno DICHIARATI nel memo
    if tool_health["ko"]:
        _hline = ("\n\n=== HEALTH-CHECK PRE-RUN: TOOL NON DISPONIBILI ===\n- "
                  + "\n- ".join(tool_health["ko"])
                  + "\nREGOLA: questi dati NON hanno alimentato la run. Il memo DEVE "
                    "dichiarare esplicitamente il pezzo mancante; VIETATE affermazioni "
                    "che presuppongono quei tool (es. decomposizione VaR se "
                    "var_contribution e' KO).")
        sizing_context = (sizing_context or "") + _hline
        _log("Health-check: " + str(len(tool_health["ko"])) + " tool KO dichiarati al Capo")

    # FRESHNESS -> prompt del Capo: i dati stantii vanno dichiarati nel memo
    try:
        from bellomberg.core.freshness import format_for_capo as _fmt_fresh
        _fline = _fmt_fresh(freshness_report)
        if _fline:
            sizing_context = (sizing_context or "") + _fline
            _log("Freshness: " + str(len(freshness_report["stale"])) + " dati STALE dichiarati al Capo")
    except Exception as e:
        _log("[!] freshness inject skipped: " + str(e))

    # CAPO synthesis (con memoria persistente)
    _log("=" * 60)
    _log("CAPO synthesis (memory-aware)")
    _log("=" * 60)
    bb.mark_specialist_start("capo", 3)
    _capo_t0 = time.perf_counter()
    memo, capo_usage = run_capo(bb, portfolio_data=portfolio, memory_db=db, sizing_context=sizing_context, scoring_context=scoring_context)
    _capo_dur = time.perf_counter() - _capo_t0
    # Il Capo entra nel conto costi come gli specialisti. cache_ttl=None: non usa
    # prompt caching (capo.py, messages.create senza cache_control).
    # Se run_capo ha restituito il dict d'errore (0/0 token), la riga nasce api_error:
    # un Capo morto si dichiara, non sparisce dai costi.
    _record_capo_usage(bb, capo_usage, _capo_dur)
    bb.mark_specialist_done("capo")

    # MEMO LINTER (audit/07 P2, v1 SOLO FLAG): check deterministici su % del NAV,
    # somma scenari, quadratura dry powder, tag [src]. Gira PRIMA del validator
    # cosi' analizza il memo pulito; numeri del Capo intoccati.
    try:
        from bellomberg.reporting.memo_linter import build_linter_block
        _lblock = build_linter_block(memo, portfolio)
        if _lblock:
            memo = memo + "\n\n" + _lblock
            _log("MEMO LINTER: " + str(_lblock.count("\n- ")) + " avvertimenti aggiunti al memo")
        else:
            _log("MEMO LINTER: nessun avvertimento")
    except Exception as e:
        _log("[!] MEMO LINTER skipped: " + str(e))

    # ACTION VALIDATOR #191 (v1 SOLO FLAG, scelta PM 13/07): appende avvertimenti al
    # memo (sizing sforato, riproposte mai eseguite); i numeri del Capo restano
    # intoccati e un guasto del validator non tocca la run.
    try:
        from bellomberg.agents.action_validator import build_validator_block
        _vblock = build_validator_block(memo, bb.data.get("_sizing"), db, exclude_memo_id=memo_id)
        if _vblock:
            memo = memo + "\n\n" + _vblock
            _log("ACTION VALIDATOR: " + str(_vblock.count("\n- ")) + " avvertimenti aggiunti al memo")
        else:
            _log("ACTION VALIDATOR: nessun avvertimento")
        # #204b HARD (Lotto C verita' dei numeri, ok PM 23/07; riprogettato dopo
        # review): qui SOLO la DETECT (blocco dichiarato nel memo/PDF) — l'APPLY
        # al registro decisioni sta DOPO extract_and_save_decisions, piu' sotto
        # (le decisioni a questo punto NON esistono ancora in DB).
        from bellomberg.agents.action_validator import detect_sanity_exclusions
        _xblock, _sanity_pairs = detect_sanity_exclusions(memo)
        if _xblock:
            memo = memo + "\n\n" + _xblock
            _log("ACTION VALIDATOR: " + str(_xblock.count("\n- ")) + " righe su modelli BLOCK dichiarate nel memo")
    except Exception as e:
        _log("[!] ACTION VALIDATOR skipped: " + str(e))
        _sanity_pairs = []

    # QUALITA' DATI nel memo (21/07, lezione run #46): i 24 STALE erano dichiarati al
    # Capo ma ASSENTI dal memo — l'obbligo di dichiarazione non puo' dipendere dalla
    # disciplina dell'LLM: come linter/validator, il blocco lo appende il CODICE.
    # (regenerate_memo non ha il freshness_report post-mortem: la' il blocco manca,
    # dichiarato in questo commento.)
    try:
        from bellomberg.core.freshness import format_for_memo as _fmt_fresh_memo
        _fblock = _fmt_fresh_memo(freshness_report)
        if _fblock:
            memo = memo + "\n\n" + _fblock
            _log("QUALITA' DATI: " + str(len(freshness_report["stale"]))
                 + " STALE dichiarati nel memo (blocco automatico)")
    except Exception as e:
        _log("[!] blocco QUALITA' DATI skipped: " + str(e))

    # Save memo markdown archivio
    os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)
    md_path = os.path.join(RESEARCH_NOTES_DIR,
                            "bellomberg_" + datetime.now().strftime("%Y%m%d_%H%M") + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(memo)
    _log("Markdown archive: " + md_path)

    # REFLECTION #210 (post-run, Sonnet): lezione sintetica ancorata agli esiti
    # dello scorekeeper, salvata per il priming della PROSSIMA run. Best-effort.
    _lesson = None
    _progress_reflection_status = "unavailable"
    try:
        from bellomberg.agents.reflection import generate_lesson
        # #44/finding 4: questa chiamata Sonnet e' REALE e prima di oggi non entrava
        # nel conto della run -> il totale si presentava completo mentendo per
        # omissione (fallback silenzioso, regola PM 14\07). usage_out la fa uscire.
        # try/finally come il ramo _action_table (erano asimmetrici): _record_side_usage
        # stava DENTRO il try, quindi se generate_lesson sollevava DOPO aver bruciato i
        # token (es. _save_lessons su disco pieno) l'except sotto ingoiava tutto e quei
        # token sparivano dal conto — mentre _refl_usage, mutato in-place, li aveva gia'.
        # Spesa avvenuta e nota = spesa contata (principio contabile 1).
        _refl_usage = {}
        try:
            _lesson = generate_lesson(memo, memo_id=memo_id, usage_out=_refl_usage)
        finally:
            _record_side_usage(bb, "_reflection", _refl_usage)
        if _lesson:
            _progress_reflection_status = "generated"
            _log("Reflection #210: lezione salvata per la prossima run ("
                 + str(len(_lesson)) + " char)")
        else:
            _progress_reflection_status = "not_generated"
            _log("Reflection #210: nessuna lezione (dichiarato nel log del modulo)")
    except Exception as e:
        _log("[!] reflection skipped: " + str(e))

    # Blackboard JSON archive
    debug_path = md_path.replace(".md", "_blackboard.json")
    try:
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump({"data": bb.data, "tool_log": bb.tool_log}, f, indent=2, default=str)
    except Exception:
        pass

    # PDF MEMO PRINCIPALE (#183 istituzionale Aurum-style, fallback al builder classico)
    pdf_memo_path = None
    nav_history = None
    if risk_data is None:  # riusa quello del sizing; ricalcola solo se manca
        try:
            from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
            risk_data = compute_portfolio_risk()
            if isinstance(risk_data, dict) and risk_data.get("error"):
                _log("  [!] risk metrics: " + str(risk_data.get("error")))
                risk_data = None
        except Exception as e:
            _log("[!] risk metrics for PDF skipped: " + str(e))
    try:
        from bellomberg.portfolio.portfolio_analytics import compute_nav_history
        nav_history = compute_nav_history()
        if isinstance(nav_history, dict) and nav_history.get("error"):
            nav_history = None
    except Exception as e:
        _log("[!] nav history for PDF skipped: " + str(e))
    try:
        from bellomberg.reporting.pdf_institutional import build_institutional_memo
        pdf_memo_path = build_institutional_memo(
            memo_markdown=memo, portfolio_data=portfolio,
            risk_data=risk_data, nav_history=nav_history,
            sizing_data=bb.data.get("_sizing"),
            scoring_data=bb.data.get("_score_cache"))
        if pdf_memo_path:
            _log("PDF MEMO (istituzionale): " + pdf_memo_path)
    except Exception as e:
        _log("[!] PDF istituzionale error: " + str(e))
    if not pdf_memo_path:  # fallback al builder classico (mai senza PDF)
        try:
            from bellomberg.reporting.pdf_report import build_pdf_report
            pdf_memo_path = build_pdf_report(
                memo_markdown=memo, portfolio_data=portfolio,
                macro_data=macro, options_data_dict={})
            if pdf_memo_path:
                _log("PDF MEMO (classico fallback): " + pdf_memo_path)
        except Exception as e:
            _log("[!] PDF memo fallback error: " + str(e))

    # PDF APPENDICE QUANT
    pdf_appendix_path = None
    try:
        try:
            from bellomberg.reporting.charts_quant import build_quant_appendix_v2 as build_quant_appendix  # #178
        except ImportError:
            from bellomberg.reporting.charts_agent import build_quant_appendix
        pdf_appendix_path = build_quant_appendix(
            blackboard=bb, portfolio_data=portfolio,
            macro_data=macro, options_data_dict={},
            correlation_data=correlation_matrix)
        if pdf_appendix_path:
            _log("PDF APPENDICE: " + pdf_appendix_path)
    except Exception as e:
        _log("[!] PDF appendix error: " + str(e))

    # DCF EXCEL FILES
    dcf_files = _collect_dcf_files(start_time)
    if dcf_files:
        _log("DCF Excel files: " + str(len(dcf_files)))

    # UPDATE memo in DB con paths finali + chunks ChromaDB
    if db and memo_id:
        try:
            # Update memo row con paths e full markdown
            with db._conn() as conn:
                conn.execute("""UPDATE memos SET full_markdown=?, pdf_path=?, appendix_path=?,
                                dcf_files=?, capo_tokens_in=?, capo_tokens_out=? WHERE id=?""",
                              (memo, pdf_memo_path, pdf_appendix_path,
                               json.dumps(dcf_files), capo_usage["input_tokens"],
                               capo_usage["output_tokens"], memo_id))
            # Re-embed in ChromaDB
            if db.col_memos:
                chunks = db._chunk_markdown(memo)
                if chunks:
                    # audit/11 §2: con gli stessi id chromadb add() MANTIENE il documento
                    # vecchio -> il chunk 0 restava '[IN PROGRESS]' per sempre. upsert.
                    db.col_memos.upsert(
                        documents=chunks,
                        metadatas=[{"memo_id": memo_id, "chunk_idx": i} for i in range(len(chunks))],
                        ids=["memo_" + str(memo_id) + "_chunk_" + str(i) for i in range(len(chunks))]
                    )
            # Extract & save decisions from ACTION TABLE
            # #44/finding 4: dentro c'e' una chiamata Sonnet REALE (#200c estrazione
            # strutturata) che prima non entrava nel conto della run. Registrata QUI,
            # cioe' PRIMA di save_llm_usage sotto, altrimenti finirebbe nell'heartbeat
            # ma non nel DB.
            _at_usage = {}
            try:
                decision_ids = db.extract_and_save_decisions(memo_id, memo,
                                                             usage_out=_at_usage)
                # Link newly extracted proposals to the exact generation reviewed
                # by this committee. Missing metadata is declared, never backfilled.
                try:
                    with db._conn() as conn:
                        candidates = conn.execute("SELECT id,ticker FROM decisions WHERE memo_id=?", (memo_id,)).fetchall()
                    for decision_id, ticker in candidates:
                        result = bb.valuation_results.get(str(ticker).upper())
                        if result and result.get("snapshot_id"):
                            db.link_valuation_snapshot(result["snapshot_id"], generation_id=result["generation_id"],
                                                       decision_id=decision_id)
                except Exception as exc:
                    _log("[!] Collegamento snapshot valutazione/decisione non salvato: " + str(exc))
                _log("Decisions extracted from ACTION TABLE: " + str(len(decision_ids)))
            finally:
                _record_side_usage(bb, "_action_table", _at_usage)
            # #204b HARD fase APPLY (dopo il salvataggio: ora le righe ESISTONO)
            try:
                if _sanity_pairs:
                    from bellomberg.agents.action_validator import apply_sanity_exclusions
                    _n_x = apply_sanity_exclusions(db, memo_id, _sanity_pairs)
                    _log("ACTION VALIDATOR: esclusioni HARD applicate al registro: "
                         + str(_n_x) + "/" + str(len(_sanity_pairs)))
                    if _n_x < len(_sanity_pairs):
                        _log("[!] esclusioni HARD: " + str(len(_sanity_pairs) - _n_x)
                             + " righe BLOCK NON trovate nel registro (parser/azione "
                             "diversa?) — restano dichiarate solo nel memo")
            except Exception as _xe:
                _log("[!] esclusioni HARD non applicate: " + str(_xe))
        except Exception as e:
            _log("[!] DB memo finalize failed: " + str(e))
        # Consumo LLM riga per riga (agente+round). Try separato: le colonne
        # memos.capo_tokens_in/out restano (le leggono altri), questo e' il dettaglio.
        try:
            _n_usage = db.save_llm_usage(memo_id, bb.usage_log)
            _log("LLM usage rows saved: " + str(_n_usage))
        except Exception as e:
            _log("[!] save_llm_usage failed: " + str(e))

    # EMAIL
    all_attachments = []
    if pdf_memo_path: all_attachments.append(pdf_memo_path)
    if pdf_appendix_path: all_attachments.append(pdf_appendix_path)
    all_attachments.extend(dcf_files)
    _send_weekly_email(all_attachments)

    bb.mark_run_complete()
    # Progressi: conserva la misura già acquisita nella run. Nessun ricalcolo,
    # retrodatazione o ulteriore chiamata LLM. Schema assente = buco dichiarato.
    try:
        from bellomberg.agents.score_history import record_completed_run
        _progress_result = record_completed_run(
            db, bb, scorecard=_progress_scorecard, score_error=_progress_score_error,
            lesson=_lesson, reflection_status=_progress_reflection_status)
        _log("Progressi agenti: " + _progress_result["reason"])
    except Exception as e:
        _log("[!] Progressi agenti NON registrati: " + type(e).__name__ + ": " + str(e))
    elapsed = (datetime.now() - start_time).total_seconds()
    _log("=" * 60)
    _log(BRAND_NAME + " v" + VERSION + " - DONE in " + str(int(elapsed)) + "s")
    _log("Capo tokens: in=" + str(capo_usage["input_tokens"]) + " out=" + str(capo_usage["output_tokens"]))
    # Costo della run: UN SOLO PUNTO DI VERITA'. Prima qui si RICALCOLAVA il totale con
    # una regola propria (somma per entry) mentre la UI leggeva quello del blackboard
    # (somma per agente): sulla stessa run 0,58 EUR a schermo e 1,12 EUR nel log, senza
    # una riga che spiegasse il delta. Ora il log NON calcola piu' nulla: chiede a
    # bb._usage_aggregates() lo STESSO totale che finisce nell'heartbeat e nella UI, e
    # lo stampa. Se le due cifre divergeranno ancora sara' un bug dell'aggregatore, non
    # due contabilita' parallele. Lock preso (RLock) per uno snapshot consistente.
    try:
        from bellomberg.core.llm_pricing import format_eur
        with bb._lock:
            _by, _total = bb._usage_aggregates()
        _unpriced = list(_total.get("unpriced_agents") or [])
        _errors = list(_total.get("error_agents") or [])
        # partial e' la chiave in contratto; se assente, l'informazione equivalente e'
        # la presenza di non prezzabili (v2: partial <=> almeno una entry senza costo).
        _partial = _total.get("partial", bool(_unpriced))
        _line = "Costo run: " + format_eur(_total.get("cost_eur"))
        if _partial:
            _line += " (PARZIALE: e' un MINIMO, non il costo pieno)"
        if _unpriced:
            _line += " - non prezzabili: " + ", ".join(str(a) for a in _unpriced)
        if _errors:
            # lista SEPARATA: "non so quanto" != "e' andato KO"
            _line += " - in errore: " + ", ".join(str(a) for a in _errors)
        _line += " [FX: " + str(_total.get("fx_source")) + "]"
        _log(_line)
    except Exception as e:
        # Best-effort: il buco si DICHIARA, ma la run finisce lo stesso.
        _log("[!] Costo run non calcolabile (aggregazione fallita): " + str(e))
    _log("Memo ID in DB: #" + str(memo_id))
    _log("Decisioni auto-estratte salvate nel DB (tabella decisions): GET /decisions o la pagina Decisions dell'app")


if __name__ == "__main__":
    try:
        run_multi_agent()
    except BaseException as e:
        # Heartbeat ONESTO anche su crash (bug PM 15/07): senza questo, una run
        # morta a meta' lascia current_run.json su "running: true" per sempre e
        # Agents Live mostra una run fantasma a ogni apertura dell'app.
        try:
            # 27/08: stessa via atomica del heartbeat (temporaneo + os.replace):
            # anche qui un open("w") diretto poteva lasciare al lettore un file
            # a meta' (review del lotto heartbeat). Blocco __main__: non coperto
            # da test, dichiarato.
            from bellomberg.agents.specialists.base import Blackboard as _B, scrivi_file_atomico as _sfa
            _ok, _err = _sfa(_B.HEARTBEAT_PATH, json.dumps({
                "running": False,
                "message": "Run TERMINATA con errore (vedi data/consigliere_run.log): "
                           + ((_MOTIVO_USCITA["testo"] if isinstance(e, SystemExit) and _MOTIVO_USCITA["testo"]
                               else str(e))[:200]),
                "completed_at": datetime.now().isoformat(timespec="seconds")}))
            if not _ok:
                # l'esito non si ignora (review 27/08): un heartbeat di crash non
                # scritto lascia F4 su «running: true» — almeno lo dice il log
                print("[!] heartbeat di crash NON scritto (F4 restera' su running): "
                      + str(_err), flush=True)
        except Exception:
            pass
        raise  # rc != 0 preservato per il watchdog della API
