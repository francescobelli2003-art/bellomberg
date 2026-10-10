"""Checkpoint and delivery adapters used by the normal Consigliere entry point."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from bellomberg.storage.weekly_run_store import (
    CONTRACT_VERSION, WeeklyRunBlocked, WeeklyRunStore, digest, current_book_identity, costs_unresolved)
from bellomberg.core.research_analysis import (
    RESEARCH_ANALYSIS_MODE, is_research_mode, research_reference, research_digest)


def book_identity(db):
    """Read holdings and cash identity without fetching prices, FX or provider data."""
    return current_book_identity(db)


def require_existing_database(path):
    """Do not let a wrong configured path silently bootstrap an empty paid run."""
    import sqlite3
    from contextlib import closing
    target = Path(path).resolve()
    if not target.is_file():
        raise WeeklyRunBlocked("DB configurato assente: run bloccata prima di inizializzazione e AI")
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as conn:
            for table in ("positions", "cash_state", "memos"):
                conn.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchall()
    except sqlite3.Error as exc:
        raise WeeklyRunBlocked("DB configurato illeggibile o schema non riconosciuto: " + type(exc).__name__) from exc


def create_run(db, portfolio, mandate, contract, language):
    if (not isinstance(portfolio, dict) or portfolio.get("error") or
            not isinstance(portfolio.get("positions"), list) or
            isinstance(portfolio.get("n_positions"), bool) or
            portfolio.get("n_positions") != len(portfolio["positions"])):
        raise WeeklyRunBlocked("Fonte portfolio non verificata: nessuna richiesta AI consentita")
    memo_id = db.save_memo("[IN PROGRESS]", portfolio_nav_eur=portfolio.get("nav_total_eur"),
                           title="Bellomberg Weekly - " + datetime.now().strftime("%d/%m/%Y"),
                           output_language=language)
    if not memo_id:
        raise WeeklyRunBlocked("Persistenza memo non confermata: nessuna richiesta AI consentita")
    context = {"contract_version": CONTRACT_VERSION, "portfolio": portfolio,
               "book_identity": book_identity(db), "mandate_sha256": digest(mandate),
               "contract": contract, "language": language,
               "research_started_at": datetime.now(timezone.utc).isoformat()}
    return WeeklyRunStore(db, memo_id, context=context)


def validate_resume(store, mandate, contract):
    if digest(mandate) != store.context["mandate_sha256"]:
        raise WeeklyRunBlocked("Mandato cambiato: analisi storica conservata, nuova autorizzazione necessaria")
    if contract != store.context["contract"]:
        raise WeeklyRunBlocked("Configurazione o contratto cambiato: checkpoint non riutilizzabile")
    if book_identity(store.db) != store.context["book_identity"]:
        raise WeeklyRunBlocked("Book cambiato: nessun mix tra report storici e portafoglio attuale; recupera solo la consegna")


def bind_blackboard(bb, store):
    bb.run_id = store.run_id
    bb.weekly_store = store
    store.blackboard = bb
    bb.specialist_checkpoints = {}
    store.restore(bb)
    mode = store.context['contract'].get('analysis_mode')
    if mode not in (None, RESEARCH_ANALYSIS_MODE):
        raise WeeklyRunBlocked('Unknown weekly analysis mode: archived context was preserved')
    bb.analysis_mode = mode
    if is_research_mode(bb):
        bb.research_tickers = sorted({row['ticker'] for row in store.context['portfolio']['positions']
                                      if isinstance(row, dict) and row.get('ticker')})
        validate_research_artifact_scope(bb, store.get('artifact_bundle'))
    bb.data["_research_context"] = {"research_started_at": store.context["research_started_at"],
                                     "portfolio": "original_run_snapshot",
                                     "operational_revalidation": "required_before_execution"}
    bb._weekly_error = None

    def persist(event, payload):
        store.save_snapshot(bb)

    def blocked():
        if bb._weekly_error is not None:
            raise bb._weekly_error

    def fail(error, *, desk=None, round_n=None, request_id=None, **kwargs):
        # Comitato «a lacune» (decisioni PM 04/10): il guasto LOCALE di un desk o del Red
        # Team e' una lacuna dichiarata, non la fine di una run gia' pagata. Resta errore
        # di RUN tutto il resto: integrita', crash.
        # REGOLA PM 05/10 (prevale sul 04/10, R-0RT F-A): un costo incerto nel registro NON
        # trasforma piu' la lacuna in errore di run: si DICHIARA nel messaggio della lacuna e il
        # costo resta incerto (da riconciliare). Nessun reinvio: il desk/Red Team in lacuna non
        # rigira e la riga incerta resta 'unknown' nel registro.
        kind = gap_kind(bb, store, error, desk)
        if kind is not None:
            incerto = ("; costo incerto nel registro richieste (DICHIARATO, da riconciliare; nessun "
                       "reinvio automatico)" if _costs_uncertain(store) else "")
            item = {"message": type(error).__name__ + ": " + str(error)[:2800] + incerto,
                    **({"cost_uncertain": True} if incerto else {}),
                    "exception_type": type(error).__name__, "desk": desk, "round": round_n,
                    "request_id": request_id or getattr(error, "request_id", None),
                    "phase": "red_team" if kind == "red_team" else "round_" + str(round_n)}
            with bb._lock:
                if kind == "red_team":
                    bb.data.setdefault("_red_team_gap", item)   # la PRIMA causa resta
                else:
                    bb.data.setdefault("_desk_gaps", {}).setdefault(desk, item)
                gaps = json.loads(json.dumps(bb.data.get("_desk_gaps") or {}, default=str))
                red_gap = bb.data.get("_red_team_gap")
            store.update(desk_gaps=gaps, red_team_gap=red_gap)
            store.save_snapshot(bb)
            return
        if bb._weekly_error is None:
            bb._weekly_error = error
        store.fail(error, desk=desk, round_n=round_n)
        store.save_snapshot(bb)

    def should_run(name, round_n):
        if is_research_mode(bb) and name in (bb.data.get("_desk_gaps") or {}):
            return False   # desk in lacuna: fuori per il resto della run (anche in ripresa)
        saved = store.get("desk:" + name + ":" + str(round_n))
        if saved is None:
            return True
        report = bb.read(name, round_n)
        if not isinstance(report, str) or sha256(report.encode("utf-8")).hexdigest() != saved["report_sha256"]:
            raise WeeklyRunBlocked("Report completo modificato: " + name + " R" + str(round_n))
        if round_n == 2 and is_research_mode(bb):
            validate_research_reviews(bb, [name])
        return False

    def complete(name, round_n, report, status):
        from bellomberg.agents.capo import _e_segnaposto
        report = report if isinstance(report, str) else bb.read(name, round_n)
        if status != "complete" or not isinstance(report, str) or not report.strip() or _e_segnaposto(report):
            error = RuntimeError(name + " R" + str(round_n) + " response incomplete: " + str(status))
            if gap_kind(bb, store, error, name) is None:
                raise WeeklyRunBlocked("Report incompleto " + name + " R" + str(round_n) + ": " + str(status))
            fail(error, desk=name, round_n=round_n)
            # Il testo troncato/inutilizzabile non arriva al Capo come report: al suo posto
            # il segnaposto, cosi' scatta DOMINI SCOPERTI (R0/R1) o ROUND PERSO (R2).
            # Il testo resta nel checkpoint del desk e nel registro dei report.
            bb.write(name, round_n, "[ERROR " + name + " round " + str(round_n)
                     + "]: lacuna dichiarata, risposta " + str(status))
            return
        store.complete("desk:" + name + ":" + str(round_n),
                       {"report_sha256": sha256(report.encode("utf-8")).hexdigest(), "status": status}, bb)

    bb.persist_run_checkpoint = persist
    bb.raise_if_run_blocked = blocked
    bb.record_run_failure = fail
    bb.should_run_specialist = should_run
    bb.record_specialist_completion = complete


def validate_research_reviews(bb, desks):
    reference = research_reference(bb)
    for name in desks:
        saved = bb.data.get('_desk_research_reviews', {}).get(name)
        report = bb.read(name, 2)
        if (not isinstance(saved, dict) or saved.get('round') != 2
                or saved.get('research_ref') != reference or not isinstance(report, str)
                or saved.get('report_sha256') != research_digest(report)):
            raise WeeklyRunBlocked('Final desk review is not bound to the sealed research: ' + name)
    return reference


# ---------------------------------------------------------------------------------------
# Comitato «a lacune» (decisioni PM 04/10, pacchetto consigliato; KB Opus 5.5).
# Quorum = Fundamentals + almeno 4 dei 6 desk (come Trade Idea; con un roster piu' corto
# servono tutti: weekly_gaps.quorum_threshold); desk caduto = lacuna dichiarata e fuori
# per il resto della run; Red Team mancante = si prosegue, niente convinzione ALTA; Capo
# fallito = memo parziale INCOMPLETO, nessuna decisione, nessuna email; costo ignoto
# resta bloccante. Le lacune valgono solo per la modalita' research (come Trade Idea):
# le run Excel in archivio restano fail-stop.

RED_TEAM_UNUSABLE = "Red Team critique unusable: "


def _costs_uncertain(store):
    """True se il registro richieste ha costi incerti O non e' verificabile (mai «ok» per default)."""
    journal = getattr(store, "request_journal", None)
    if journal is None:
        return True
    try:
        return costs_unresolved(journal.summary() or {})
    except Exception:
        return True


def _red_team_local_failure(error):
    """Allow-list del Red Team: errore del provider sulla sua richiesta, risposta incompleta
    o critica inutilizzabile. Integrita' (checkpoint/contratto cambiati, esito tool ignoto),
    errori di programmazione e crash restano errori di run.
    V0-REDTEAM (decisione PM/main 05/10): anche il preventivo fallito PRIMA dell'invio
    (RedTeamSenzaPreventivo, riconosciuto per TIPO: nessuna spesa per costruzione), compreso
    il modello configurato assente dal catalogo o un listino anomalo (decisione main 06/10:
    configurazione del Red Team = lacuna dichiarata, come per la sonda «red team best-effort»)."""
    from bellomberg.core.llm_client import APIError
    from bellomberg.agents.red_team import RedTeamSenzaPreventivo
    if isinstance(error, (APIError, RedTeamSenzaPreventivo)):
        return True
    message = str(error)
    return ((isinstance(error, ValueError) and message.startswith("Red Team incomplete: "))
            or (isinstance(error, RuntimeError) and message.startswith(RED_TEAM_UNUSABLE)))


def gap_kind(bb, store, error, desk):
    """'desk' | 'red_team' se il guasto e' una lacuna dichiarabile, altrimenti None (run-level).
    V0-REDTEAM (PM/main 05/10): la lacuna del Red Team vale in OGNI modalita' (anche fuori
    research); quella dei desk resta solo research (quorum del contratto research)."""
    if desk is None:
        return None
    if desk in ("red_team", "_red_team"):
        if not _red_team_local_failure(error):
            return None
        kind = "red_team"
    elif not is_research_mode(bb):
        return None
    elif desk in store.context["contract"]["roster"]:
        from bellomberg.agents.trade_idea import _desk_local_failure
        if not _desk_local_failure(error):
            return None
        kind = "desk"
    else:
        return None
    return kind   # costo incerto: dichiarato nel messaggio della lacuna (fail), non errore di run


def _require_quorum(roster, present, missing):
    from bellomberg.reporting.weekly_gaps import QUORUM_REQUIRED, quorum_threshold
    threshold = quorum_threshold(roster)
    if any(name not in present for name in QUORUM_REQUIRED) or len(present) < threshold:
        raise WeeklyRunBlocked(
            "Comitato sotto quorum (servono Fundamentals e almeno " + str(threshold) + " desk su "
            + str(len(roster)) + "; presenti " + str(len(present)) + "): nessun memo di decisione. "
            "Mancano: " + ", ".join(desk + " (" + str(reason)[:160] + ")" for desk, reason in missing.items()))


def quorum_still_reachable(bb, store):
    """Dopo R0 e R1: ci si ferma PRIMA di pagare round inutili se il quorum e' gia' perso."""
    if not is_research_mode(bb):
        return
    roster = list(store.context["contract"]["roster"])
    gaps = bb.data.get("_desk_gaps") or {}
    _require_quorum(roster, [desk for desk in roster if desk not in gaps],
                    {desk: (item or {}).get("message") or "desk failure"
                     for desk, item in gaps.items() if desk in roster})


def committee_quorum(bb, store):
    """(present, missing) per il sigillo: un desk in lacuna o senza R1 utilizzabile e' mancante."""
    from bellomberg.agents.capo import _e_segnaposto
    roster = list(store.context["contract"]["roster"])
    gaps = bb.data.get("_desk_gaps") or {}
    missing = {}
    for desk in roster:
        report = bb.read(desk, 1)
        if desk in gaps:
            missing[desk] = (gaps[desk] or {}).get("message") or "desk failure"
        elif not isinstance(report, str) or not report.strip() or _e_segnaposto(report):
            missing[desk] = "R1 report unavailable"
    present = [desk for desk in roster if desk not in missing]
    _require_quorum(roster, present, missing)
    return present, missing


def reviewed_desks(bb, desks):
    """I desk R2 di cui pretendere la revisione legata al sigillo: non i desk in lacuna
    (caduti dopo il sigillo: resta il loro R1) ne' quelli sigillati come mancanti."""
    sealed = bb.data.get("_research_thesis") or {}
    gaps = bb.data.get("_desk_gaps") or {}
    return [name for name in desks if name not in gaps
            and name not in (sealed.get("missing_reports") or {})]


def released_retries(store):
    """Ritentativi non fatturati dal registro (interfaccia KA): lista o None = non misurato."""
    journal = getattr(store, "request_journal", None)
    try:
        rows = (journal.summary() or {}).get("released_requests") if journal is not None else None
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    return [{"request_id": row.get("request_id"), "agent": row.get("agent"), "reason": row.get("reason")}
            for row in rows if isinstance(row, dict)]


def committee_summary(bb, store, *, memo=None, usage=None):
    """Riepilogo del comitato (reporting.weekly_gaps) salvato in blackboard e nello stato.
    Un guasto del calcolo si DICHIARA (chiave error), mai un comitato «completo» per default."""
    from bellomberg.reporting.weekly_gaps import committee_gaps_summary
    try:
        summary = committee_gaps_summary(
            bb, store, capo=None if usage is None else {"memo": memo, "usage": usage},
            unbilled_retries=released_retries(store), language=store.context.get("language"))
    except Exception as exc:
        summary = {"status": "unavailable", "error": type(exc).__name__ + ": " + str(exc)[:300]}
    with bb._lock:
        bb.data["_committee_gaps"] = summary
    store.update(committee_gaps={key: value for key, value in summary.items() if key != "memo_markdown"})
    return summary


def committee_memo_block(summary):
    if summary.get("status") == "unavailable":
        return ("## Comitato: stato non calcolato\nRiepilogo delle lacune del comitato non calcolato ("
                + summary.get("error", "n.d.") + "): completezza del comitato NON verificata; decisioni "
                "non ammesse (nessuna decisione registrata).")
    return summary.get("memo_markdown") or ""


def committee_email_line(bb):
    import html as _html
    summary = (getattr(bb, "data", {}) or {}).get("_committee_gaps")
    if not isinstance(summary, dict):
        return ""
    if summary.get("status") == "unavailable":
        return ("<p><strong>COMITATO: STATO NON CALCOLATO</strong>: "
                + _html.escape(summary.get("error", "n.d.")) + "</p>")
    from bellomberg.reporting.weekly_gaps import format_gaps_for_email
    return format_gaps_for_email(summary, as_html=True)


_HIGH_TO_MEDIUM = {"ALTA": "MEDIA", "HIGH": "MEDIUM"}


def cap_high_conviction(memo):
    """Red Team mancante = niente convinzione ALTA (PM 04/10), applicato dal CODICE sulla
    colonna confidence della ACTION TABLE: ALTA -> MEDIA (HIGH -> MEDIUM).
    Ritorna (memo, declassate, residue): le residue (riga non riscrivibile) si dichiarano."""
    import re
    from bellomberg.agents.action_table_extract import parse_action_table_rows
    parsed = parse_action_table_rows(memo or "")
    column = (parsed.get("columns") or {}).get("confidence")
    if column is None:
        return memo, [], []
    # Solo il valore intero ALTA/HIGH (main 05/10): «Medio-Alta» resta com'e', mai «Medio-MEDIA».
    pattern = re.compile(r"(?<![\w-])(ALTA|HIGH)(?![\w-])", re.IGNORECASE)
    lower = lambda text_: pattern.sub(lambda m: _HIGH_TO_MEDIUM[m.group(1).upper()], text_)  # noqa: E731
    lines = (memo or "").splitlines(keepends=True)
    starts = sorted(row["line_index"] for row in parsed["rows"])
    table_end = (parsed.get("table_lines") or [None, len(lines)])[1]
    capped = []
    for row in parsed["rows"]:
        if not pattern.search(row.get("confidence") or ""):
            continue
        raw = lines[row["line_index"]]
        # stessa divisione del parser (i pipe escapati non separano le celle)
        parts = re.split(r"(?<!\\)\|", raw)
        offset = 1 if raw.strip().startswith("|") else 0
        index = column + offset
        first = str(row["cells"][column]).split(" <br> ")[0].strip()
        if index < len(parts) and parts[index].strip().replace("\\|", "|") == first:
            parts[index] = lower(parts[index])
            lines[row["line_index"]] = "|".join(parts)
        # le righe a capo indentate appartengono all'ULTIMA cella (regola del parser):
        # se la confidence e' l'ultima colonna, il declassamento vale anche per loro
        if column == len(row["cells"]) - 1:
            following = [i for i in starts if i > row["line_index"]]
            stop = min(following[0] if following else table_end, table_end)
            for i in range(row["line_index"] + 1, stop):
                lines[i] = lower(lines[i])
        capped.append({"row_index": row["row_index"], "ticker": row["ticker"], "action": row["action"]})
    memo = "".join(lines)
    # Verifica sulle righe che il REGISTRO leggera': lo stesso parser del gate di
    # pubblicazione (parse_action_table_rows -> decisions.confidence).
    residual = [{"row_index": row["row_index"], "ticker": row["ticker"], "action": row["action"]}
                for row in parse_action_table_rows(memo)["rows"]
                if pattern.search(row.get("confidence") or "")]
    left = {row["row_index"] for row in residual}
    capped = [row for row in capped if row["row_index"] not in left]
    return memo, capped, residual


def conviction_cap_block(capped, residual, language, *, comitato_non_calcolato=False):
    from bellomberg.core.language import text
    if not capped and not residual:
        return ""
    # R-0RT F-E (06/10): con il riepilogo del comitato non calcolato la presenza del Red Team NON
    # e' verificata: il declassamento resta (prudenza), ma il motivo detto e' quello vero.
    motivo = (text("Stato del comitato non calcolato: la presenza del Red Team NON e' verificata e, per "
                   "prudenza, nessuna proposta può avere convinzione ALTA. Il codice ha riscritto la "
                   "colonna confidence della ACTION TABLE.",
                   "Committee status not computed: the Red Team's presence is NOT verified and, as a "
                   "precaution, no proposal may carry HIGH conviction. The code rewrote the ACTION TABLE "
                   "confidence column.", language=language) if comitato_non_calcolato else
              text("Il Red Team non ha completato la critica: per regola del PM nessuna proposta può avere "
                   "convinzione ALTA. Il codice ha riscritto la colonna confidence della ACTION TABLE.",
                   "The Red Team did not complete its critique: by PM rule no proposal may carry HIGH "
                   "conviction. The code rewrote the ACTION TABLE confidence column.", language=language))
    lines = [text("## Comitato: convinzione ALTA non ammessa",
                  "## Committee: HIGH conviction not allowed", language=language), motivo]
    # Frase di verifica solo se la verifica e' passata (righe rilette con il parser del registro).
    lines.append(text("Verifica sulle righe estratte per il registro decisioni: nessuna riga con ALTA.",
                      "Check on the rows extracted for the decision register: no row with HIGH.",
                      language=language) if not residual else
                 text("Verifica sulle righe estratte per il registro decisioni: {} righe conservano ALTA "
                      "(elencate sotto) e vanno lette come MEDIA.",
                      "Check on the rows extracted for the decision register: {} rows keep HIGH (listed "
                      "below) and must be read as MEDIUM.", language=language).format(len(residual)))
    for row in capped:
        lines.append(text("- Riga {}: {} {}, ALTA declassata a MEDIA.", "- Row {}: {} {}, HIGH lowered to "
                          "MEDIUM.", language=language).format(row["row_index"] + 1, row["action"], row["ticker"]))
    for row in residual:
        lines.append(text("- Riga {}: {} {}, ALTA NON riscrivibile dal codice: va letta come MEDIA.",
                          "- Row {}: {} {}, HIGH could NOT be rewritten by the code: read it as MEDIUM.",
                          language=language).format(row["row_index"] + 1, row["action"], row["ticker"]))
    return "\n".join(lines)


def capo_partial_package(store, bb, module, *, memo, usage, error):
    """Capo fallito (decisione PM 04/10): memo PARZIALE marcato INCOMPLETO con i report dei
    desk non sintetizzati, il Red Team e le lacune; nessuna decisione estratta, nessuna email
    automatica. Il checkpoint 'capo' NON si scrive: una ripresa esplicita del PM puo' rifare
    il Capo. La riga memos resta '[IN PROGRESS]' (la memoria delle run successive non legge
    un memo senza decisioni)."""
    from bellomberg.agents.capo import scegli_report_specialisti
    from bellomberg.core.language import text
    lang = store.context.get("language")
    t = lambda it, en: text(it, en, language=lang)  # noqa: E731
    summary = committee_summary(bb, store, memo=memo, usage=usage)
    reason = str(error)
    chosen = scegli_report_specialisti(bb.data, getattr(bb, "orari_report", None))
    labels = {"macro": "Macro", "eventdesk": "Event Desk", "crypto": "Crypto",
              "fundamentals": "Fundamentals", "quant": "Quant", "options": "Options"}
    parts = [t("# MEMO INCOMPLETO: il Capo non ha completato la sintesi",
               "# INCOMPLETE MEMO: the Capo did not complete the synthesis"),
             "",
             t("**Esito tecnico.** {}. Questo documento NON e' un memo di decisione: nessuna decisione e' "
               "stata estratta e nessuna email automatica e' partita. Sotto, il materiale del comitato "
               "NON sintetizzato.",
               "**Technical outcome.** {}. This document is NOT a decision memo: no decision was "
               "extracted and no automatic email was sent. Below, the committee material NOT "
               "synthesised.").format(reason.rstrip(".")),
             "",
             t("## Report dei desk (non sintetizzati)", "## Desk reports (not synthesised)")]
    for desk in store.context["contract"]["roster"]:
        entry = chosen.get(desk)
        parts += ["", "### " + labels.get(desk, desk) + (
            " (R" + str(entry["round"]) + ")" if entry and isinstance(entry.get("round"), int) else ""), ""]
        parts.append(entry["report"] if entry else t("[NESSUN REPORT a registro]", "[NO REPORT on record]"))
    red = bb.data.get("_red_team") or {}
    critique = red.get(max(red, key=lambda k: int(k) if str(k).isdigit() else -1)) if red else None
    parts += ["", t("## Red Team", "## Red Team"), "",
              str(critique) if critique else t("[nessuna critica a registro]", "[no critique on record]")]
    block = committee_memo_block(summary)
    if block:
        parts += ["", block]
    document = "\n".join(parts)
    digest_ = sha256(document.encode("utf-8")).hexdigest()
    md_path = Path(module.RESEARCH_NOTES_DIR) / (
        "bellomberg_memo_" + str(store.memo_id) + "_INCOMPLETO_" + digest_[:12] + ".md")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    write_frozen_text(md_path, document)
    pdf_path, pdf_error = None, None
    try:
        from bellomberg.reporting.pdf_institutional import build_institutional_memo
        pdf_path = render_pdf_once(store, module, build_institutional_memo, role="incompleto_" + digest_[:12],
            memo_markdown=document, portfolio_data=store.context["portfolio"], risk_data=None,
            nav_history=None, sizing_data=None, scoring_data=None,
            title_date=store.context["research_started_at"].split("T")[0])
        if pdf_path is None:
            pdf_error = "renderer senza PDF"
    except Exception as exc:
        pdf_error = type(exc).__name__ + ": " + str(exc)[:200]
    cause = WeeklyRunBlocked(t("Capo non completo ({}): memo parziale INCOMPLETO, nessuna decisione, "
                               "nessuna email automatica",
                               "Capo incomplete ({}): partial INCOMPLETE memo, no decision, no "
                               "automatic email").format(reason[:300]))
    store.fail(cause, phase="capo")
    usage_error = None
    try:
        persist_usage_once(store, bb)
    except Exception as exc:
        usage_error = type(exc).__name__ + ": " + str(exc)[:200]
    store.update(analytical_status="partial", phase="capo_partial", capo_partial={
        "reason": reason[:2000], "markdown_path": str(md_path.resolve()), "markdown_sha256": digest_,
        "pdf_path": pdf_path, "pdf_error": pdf_error, "usage_error": usage_error,
        "capo_text_sha256": sha256(str(memo or "").encode("utf-8")).hexdigest(),
        "capo_usage": usage, "decisions": "none", "email": "not_sent"})
    try:
        bb.mark_run_complete(technical_status="incomplete", reason=str(cause))
    except Exception:
        pass   # heartbeat: lo stato vero e' nello store (store.fail sopra)
    return store.status()


def build_weekly_delivery(bb, *, roots):
    if is_research_mode(bb):
        validate_research_artifact_scope(bb)
        return {'schema_version': 1, 'analysis_mode': RESEARCH_ANALYSIS_MODE,
            'completion_contract': 'research-memo-pdf/1', 'research_ref': research_reference(bb),
            'workbook_status': 'not_required', 'valuations': [], 'attempts': [],
            'attachments': [], 'expected_hashes': {}, 'email_status': 'not_attempted'}
    from bellomberg.reporting.valuation_delivery import build_manifest
    return build_manifest(bb.valuation_results, roots=roots, attempts=bb.valuation_attempts)


def validate_research_artifact_scope(bb, bundle=None):
    if not is_research_mode(bb):
        return
    if bb.valuation_results or bb.valuation_attempts or bb.valuation_generations:
        raise WeeklyRunBlocked('Research run unexpectedly contains workbook activity; preserve and review it')
    for artifact in (bundle or {}).get('artifacts', []):
        allowed = {'Markdown': '.md', 'PDF': '.pdf'}
        kind = artifact.get('kind')
        if kind not in allowed or Path(artifact.get('path', '')).suffix.lower() != allowed[kind]:
            raise WeeklyRunBlocked('Research archive contains unrequested workbook activity; restore blocked')


def weekly_email_body(bb, delivery):
    # Comitato a lacune (PM 04/10): la riga «COMITATO INCOMPLETO» apre il corpo.
    gaps_line = committee_email_line(bb)
    if is_research_mode(bb):
        reference = research_reference(bb)
        if delivery.get('research_ref') != reference:
            raise WeeklyRunBlocked('Delivery refers to a different research dossier')
        if getattr(bb, 'language', 'it') == 'en':
            return gaps_line + ('<p>Company research with explicit assumptions, source references and data gaps. '
                'Analyst consensus remains distinct from AI judgement; no AI fair value is required. '
                'The package contains the memo/PDF. Excel is not part of this analysis mode.</p>')
        return gaps_line + ('<p>Analisi societaria con assumption esplicite, fonti e dati mancanti dichiarati. '
            'Il consensus analisti resta distinto dal giudizio AI; nessun fair value AI obbligatorio. '
            'Il pacchetto contiene memo/PDF. Excel non previsto in questa modalita di analisi.</p>')
    from bellomberg.reporting.email_sender import corpo_valutazioni
    return gaps_line + corpo_valutazioni(bb.valuation_results, delivery['attachments'], delivery=delivery)


def _recovered_email_body(bb, delivery, validated):
    """Same body as the normal delivery: valuations plus the published action assessments."""
    from bellomberg.reporting.email_sender import corpo_azioni
    publication = (validated or {}).get("publication") or {}
    return weekly_email_body(bb, delivery) + "\n" + corpo_azioni(publication.get("assessments") or [])


def validate_memo(memo, usage):
    from bellomberg.agents.capo import _e_segnaposto
    if (not isinstance(memo, str) or not memo.strip() or _e_segnaposto(memo)
            or not isinstance(usage, dict) or usage.get("error") or usage.get("complete") is not True
            or any(marker in memo for marker in ("[CAPO ERROR]", "[CAPO ERROR]:", "[MEMO COLLASSATO", "[CAPO] No output"))
            or usage.get("stop_reason") != "end_turn"):
        raise WeeklyRunBlocked("Capo non completo: " + str((usage or {}).get("error") or
                                                          (usage or {}).get("stop_reason") or "memo non valido"))
    # Opus 5.5 (10/10): the paid memo survives a failed notes receipt only if it says so.
    from bellomberg.agents.capo import RICEVUTA_NOTE_MANCANTE
    if ((usage.get("research_notes_receipt") is not None or usage.get("research_notes_receipt_error"))
            and (usage.get("research_notes_receipt") != "missing" or not usage.get("research_notes_receipt_error")
                 or RICEVUTA_NOTE_MANCANTE not in memo)):
        raise WeeklyRunBlocked("Capo: ricevuta note Ricerca mancante non dichiarata nel memo")


def file_receipt(path, *, kind):
    if not path or not Path(path).is_file():
        raise WeeklyRunBlocked(kind + " richiesto assente")
    content = Path(path).read_bytes()
    if not content or (kind == "PDF" and not content.startswith(b"%PDF-")):
        raise WeeklyRunBlocked(kind + " non valido")
    return {"path": str(Path(path).resolve()), "sha256": sha256(content).hexdigest(), "kind": kind}


def verify_receipts(receipts):
    for receipt in receipts:
        path = Path(receipt["path"])
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != receipt["sha256"]:
            raise WeeklyRunBlocked("Artefatto mancante o modificato: " + path.name)


def write_frozen_text(path, text):
    from bellomberg.reporting.exact_artifacts import _create_exact
    target = Path(path)
    content = text.encode("utf-8")
    expected = sha256(content).hexdigest()
    if target.is_file():
        if sha256(target.read_bytes()).hexdigest() != expected:
            raise WeeklyRunBlocked("Originale modificato: conservato senza sovrascrittura " + target.name)
        return
    try:
        _create_exact(target, content, expected)
    except (ValueError, OSError) as exc:
        raise WeeklyRunBlocked("Originale modificato o artefatto non persistito: " + str(exc)) from exc


def validate_delivery_manifest(delivery):
    if is_research_mode(delivery):
        if (delivery.get('completion_contract') != 'research-memo-pdf/1'
                or delivery.get('workbook_status') != 'not_required'
                or not isinstance(delivery.get('research_ref'), dict)
                or delivery.get('valuations') or delivery.get('attempts')
                or delivery.get('attachments') or delivery.get('expected_hashes')):
            raise WeeklyRunBlocked('Research delivery contract contains unrequested workbook activity')
        return
    missing = [row for row in delivery.get("valuations", []) if row.get("status") != "ready"]
    if missing:
        raise WeeklyRunBlocked("Workbook richiesto non consegnabile: " + "; ".join(
            str(row.get("ticker")) + ": " + str(row.get("reason", "n.d.")) for row in missing))


def preserve_delivery_bundle(store, bb, *, md_path, pdf_path, appendix_path, delivery, allowed_roots):
    """Archive exact reviewed bytes before delivery; never compile a replacement model."""
    from bellomberg.reporting.exact_artifacts import preserve_exact_artifacts
    receipts = [dict(file_receipt(md_path, kind="Markdown"), role="memo"),
                dict(file_receipt(pdf_path, kind="PDF"), role="memo")]
    if receipts[0]["sha256"] != sha256(store.get("memo_validated")["memo"].encode("utf-8")).hexdigest():
        raise WeeklyRunBlocked("Markdown diverso dal memo validato: originale conservato")
    if appendix_path:
        receipts.append(dict(file_receipt(appendix_path, kind="PDF"), role="appendix"))
    validate_delivery_manifest(delivery)
    for path, expected in delivery.get("expected_hashes", {}).items():
        item = file_receipt(path, kind="Excel")
        if item["sha256"] != expected:
            raise WeeklyRunBlocked("Workbook modificato: copia originale conservata")
        receipts.extend([item, file_receipt(Path(path).with_suffix('.payload.json'), kind="Excel metadata")])
    vault = Path(store.db.db_path).with_name("weekly-" + store.run_id + "-artifacts")
    saved = preserve_exact_artifacts(receipts, vault, allowed_roots=[*allowed_roots, vault])
    payload = {"vault": str(vault.resolve()), "artifacts": saved}
    store.complete("artifact_bundle", payload, bb)
    store.update(artifacts=saved, artifact_status="available")
    return payload


def restore_delivery_bundle(store, module):
    from bellomberg.reporting.exact_artifacts import restore_exact_artifacts
    bundle = store.get("artifact_bundle")
    if bundle is None:
        return None
    vault = Path(store.db.db_path).with_name("weekly-" + store.run_id + "-artifacts").resolve()
    if bundle.get("vault") != str(vault):
        raise WeeklyRunBlocked("Archivio artefatti non corrisponde alla run")
    try:
        result = restore_exact_artifacts(bundle["artifacts"], vault, allowed_roots=[
            module.MODELS_DIR, module.REPORT_DIR, module.RESEARCH_NOTES_DIR, vault])
    except (ValueError, OSError) as exc:
        raise WeeklyRunBlocked("Originale modificato o copia esatta assente: " + str(exc)) from exc
    store.update(artifact_recovery=result)
    return bundle


def render_pdf_once(store, module, builder, *, role="memo", **inputs):
    """Render privately, then publish one run-specific PDF without overwriting."""
    from tempfile import NamedTemporaryFile
    from bellomberg.reporting.exact_artifacts import (
        _create_exact, preserve_exact_artifacts, restore_exact_artifacts)
    root = Path(module.REPORT_DIR).resolve()
    destination = root / ("weekly_" + str(store.memo_id) + "_" + store.run_id + "_" + role + ".pdf")
    vault = Path(store.db.db_path).with_name("weekly-" + store.run_id + "-artifacts").resolve()
    key = "rendered_pdf:" + role
    saved = store.get(key)
    if saved is not None:
        if saved.get("path") != str(destination):
            raise WeeklyRunBlocked("Percorso PDF attestato diverso dalla run")
        restore_exact_artifacts([saved], vault, allowed_roots=[root, vault])
        return str(destination)
    if destination.exists():
        raise WeeklyRunBlocked("PDF originale non attestato: conservato senza sovrascrittura")
    root.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=root, prefix=".weekly-render-", suffix=".pdf", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        generated = builder(output_path=str(temporary), **inputs)
        if generated is None:
            return None
        if Path(generated).resolve() != temporary:
            raise WeeklyRunBlocked("Renderer non ha rispettato il percorso temporaneo della run")
        receipt = file_receipt(temporary, kind="PDF")
        content = temporary.read_bytes()
        _create_exact(destination, content, receipt["sha256"])
        receipt.update(path=str(destination), role=role)
        preserve_exact_artifacts([receipt], vault, allowed_roots=[root, vault])
        store.complete(key, receipt)
        return str(destination)
    finally:
        temporary.unlink(missing_ok=True)


def deliver_once(store, module, attachments, *, body_extra, delivery, send_email,
                 acknowledge_uncertain=False):
    """Persist intent before SMTP; an ambiguous previous send is never repeated automatically.

    PM 04/10: un invio 'incerto' si ripete SOLO con la conferma esplicita del PM del possibile
    duplicato (acknowledge_uncertain=True, insieme a send_email), come Trade Idea. La conferma
    resta a registro (email_uncertain_acknowledgements) con l'esito precedente."""
    from bellomberg.reporting.valuation_delivery import record_email_outcome
    state = store.status()
    prior = state.get("email_delivery") or {}
    if prior.get("state") == "sent":
        delivery.update(prior.get("receipt") or {})
        return True
    if prior.get("state") in ("sending", "uncertain"):
        if not (acknowledge_uncertain and send_email):
            delivery.update(email_status="uncertain", smtp_state=prior.get("state"))
            store.update(delivery_status="uncertain")
            return False
        acknowledgement = {"acknowledged_at": datetime.now(timezone.utc).isoformat(), "prior": prior,
                           "outcome": "pending"}
        acknowledgements = list(state.get("email_uncertain_acknowledgements") or []) + [acknowledgement]
        store.update(email_uncertain_acknowledgements=acknowledgements)

        def record(outcome):
            acknowledgement["outcome"] = outcome
            store.update(email_uncertain_acknowledgements=acknowledgements)
        try:
            sent = _deliver_checked(store, module, attachments, body_extra=body_extra, delivery=delivery,
                                    send_email=send_email)
        except WeeklyRunBlocked as exc:
            record("blocked: " + str(exc)[:300])
            raise
        except BaseException as exc:
            record("error: " + type(exc).__name__)
            raise
        record("sent" if sent else "not_sent: " + str(delivery.get("email_status")))
        return sent
    return _deliver_checked(store, module, attachments, body_extra=body_extra, delivery=delivery,
                            send_email=send_email)


def _riga_costo_incerto(costs):
    """Riga HTML che dichiara il costo incerto nell'email (mai un costo «pieno» per default)."""
    import html as _html
    n = costs.get("unknown_requests")
    parti = []
    if isinstance(n, int) and n:
        parti.append(str(n) + (" richiesta" if n == 1 else " richieste") + " a costo/esito incerto")
    if costs.get("unavailable"):
        parti.append("registro richieste non verificabile (" + str(costs.get("error") or "n.d.") + ")")
    if not parti:
        parti.append("richieste non riconciliate nel registro")
    noto = costs.get("cost_usd")
    return ("<p><strong>COSTO DELLA RUN INCERTO</strong>: " + _html.escape("; ".join(parti))
            + ". Da riconciliare; " + ("il costo noto (" + _html.escape(str(noto)) + " USD) e' un MINIMO."
                                        if noto is not None else "costo totale n.d.") + "</p>")


def _deliver_checked(store, module, attachments, *, body_extra, delivery, send_email):
    """Controlli (decisioni, costi, bundle, ricevute) e invio, dopo la decisione su un esito precedente."""
    from bellomberg.reporting.valuation_delivery import record_email_outcome
    if not send_email:
        return False
    if store.get("decisions_finalized") is None:
        raise WeeklyRunBlocked("Decisioni non finalizzate: invio della consegna bloccato")
    costi = store.status().get("request_costs") or {}
    if costs_unresolved(costi):
        # Decisione main 06/10 (regola PM 05/10): un costo incerto NON blocca l'email: parte con il
        # costo DICHIARATO incerto in testa al corpo. Gli altri cancelli restano tutti.
        body_extra = _riga_costo_incerto(costi) + (body_extra or "")
    bundle = store.get("artifact_bundle")
    if not bundle:
        raise WeeklyRunBlocked("Bundle artefatti attestato assente: invio bloccato")
    hashes = {row["path"]: row["sha256"] for row in bundle["artifacts"]}
    paths = [str(Path(path).resolve()) for path in attachments]
    if len(set(paths)) != len(paths) or any(path not in hashes for path in paths):
        raise WeeklyRunBlocked("Allegato non attestato nel bundle originale")
    expected = {path: hashes[path] for path in paths}
    verify_receipts([{"path": path, "sha256": value} for path, value in expected.items()])
    store.update(delivery_requested=True, delivery_status="sending",
                 email_delivery={"state": "sending", "started_at": datetime.now(timezone.utc).isoformat()})
    delivery["smtp_state"] = "not_started"
    sent = module._send_weekly_email(attachments, body_extra=body_extra, delivery=delivery,
                                    expected_hashes=expected, require_all_hashes=True)
    # 'not_sent' allows a later resend: it is recorded only when SMTP provably never
    # reached DATA. An observed acceptance or a DATA phase without a True result is
    # 'uncertain' (review 04, E2: a failed outbox cleanup once turned a delivery into False).
    if sent is not True and (delivery.get("email_status") in ("uncertain", "accepted")
                             or delivery.get("smtp_state") in ("sending", "accepted")):
        delivery["email_status"] = "uncertain"
    record_email_outcome(delivery, sent)
    status = "sent" if sent is True else "uncertain" if delivery.get("email_status") == "uncertain" else "not_sent"
    receipt = {key: delivery[key] for key in ("email_status", "smtp_state", "email_error", "mime_attachments",
                                              "outbox_cleanup_error")
               if key in delivery}
    store.update(delivery_status=status, email_delivery={"state": status, "receipt": receipt})
    return sent is True


def persist_usage_once(store, bb):
    """The original aggregate rows are replaced atomically by the complete snapshot."""
    with store.db._conn() as conn:
        conn.execute("DELETE FROM llm_usage WHERE memo_id=?", (store.memo_id,))
        for row in bb.usage_log:
            conn.execute("INSERT INTO llm_usage(memo_id,agent,round_n,model,tokens_in,tokens_out,cache_read,"
                         "cache_write,api_calls,duration_s,cost_eur,fx_rate,fx_source,cost_status,cache_ttl,output_language) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (store.memo_id, row.get("agent"), row.get("round"), row.get("model") or "n.d.",
                          row.get("in"), row.get("out"), row.get("cache_read"), row.get("cache_write"),
                          row.get("api_calls"), row.get("duration_s"), row.get("cost_eur"), row.get("fx_rate"),
                          row.get("fx_source"), row.get("status"), row.get("cache_ttl"), bb.language))


def finish_status(store, bb, *, pdf_path, md_path, appendix_path, delivery, delivery_path,
                  persistence_ok, sent):
    receipts = [file_receipt(md_path, kind="Markdown"), file_receipt(pdf_path, kind="PDF")]
    if appendix_path:
        receipts.append(file_receipt(appendix_path, kind="PDF"))
    validate_delivery_manifest(delivery)
    if not persistence_ok:
        raise WeeklyRunBlocked("Persistenza finale non confermata")
    receipts += [{"path": path, "sha256": sha, "kind": "Excel"}
                 for path, sha in delivery.get("expected_hashes", {}).items()]
    bundle = store.get("artifact_bundle")
    if bundle is not None:
        receipts = bundle["artifacts"]
    verify_receipts(receipts)
    journal = getattr(store, "request_journal", None)
    costs = journal.summary() if journal is not None else store.status().get("request_costs")
    uncertain_costs = costs_unresolved(costs or {})
    if costs is not None:
        store.update(request_costs=costs, artifacts=receipts, delivery_manifest=str(delivery_path))
    prior = store.status()
    delivery_state = "sent" if sent else "uncertain" if prior.get("delivery_status") in ("sending", "uncertain") else "pending"
    # Decisione main 06/10: decisioni non ammesse dal comitato = run «incompleta» col motivo.
    not_allowed = (store.get("decisions_finalized") or {}).get("not_allowed")
    final_status = ("incomplete" if uncertain_costs or not_allowed or prior.get("delivery_requested") and not sent
                    else "completed")
    store.update(status=final_status, analytical_status="complete", artifact_status="available",
                 delivery_status=delivery_state, operational_status="requires_pm_review",   # costo incerto: incompleta, non bloccata (05/10)
                 artifacts=receipts, delivery_manifest=str(delivery_path), phase="done", last_error=None)
    bb.mark_run_complete(technical_status=final_status,
                         reason=None if final_status == "completed" else
                         "Richieste o costi incerti conservati" if uncertain_costs else
                         str(not_allowed) if not_allowed else "Consegna richiesta non confermata")
    if not_allowed:
        store.update(incomplete_reason=str(not_allowed))
    return store.status()


def recover_delivery(store, module, *, send_email=False, acknowledge_uncertain_email=False):
    """Only local rendering/persistence/transport. No analytical function is invoked."""
    from bellomberg.agents.specialists.base import Blackboard
    from bellomberg.reporting.valuation_delivery import save_manifest, record_email_outcome
    saved = store.get("memo_validated")
    if saved is None:
        raise WeeklyRunBlocked("Memo validato assente: serve una ripresa analitica autorizzata")
    memo = saved["memo"]
    validate_memo(memo, saved["capo_usage"])
    bb = Blackboard(memory_db=store.db, memo_id=store.memo_id, run_id=store.run_id)
    bind_blackboard(bb, store)
    restore_delivery_bundle(store, module)
    if is_research_mode(bb):
        from bellomberg.agents.company_research_tools import bind_weekly_company_research
        bind_weekly_company_research(bb, store)
    context = store.context
    render = store.get("render_context") or {}
    base = Path(module.RESEARCH_NOTES_DIR) / ("bellomberg_memo_" + str(store.memo_id))
    base.parent.mkdir(parents=True, exist_ok=True)
    md_path = str(base.with_suffix(".md"))
    write_frozen_text(md_path, memo)
    state = store.status()
    pdf_path = None
    appendix_path = None
    for receipt in state.get("artifacts", []):
        if receipt.get("kind") == "PDF":
            if receipt.get("role") == "appendix":
                verify_receipts([receipt])
                appendix_path = receipt["path"]
                continue
            if Path(receipt["path"]).is_file():
                verify_receipts([receipt])
                pdf_path = receipt["path"]
    if pdf_path is None:
        from bellomberg.reporting.pdf_institutional import build_institutional_memo
        pdf_path = render_pdf_once(store, module, build_institutional_memo,
            memo_markdown=memo, portfolio_data=context["portfolio"],
            risk_data=render.get("risk_data"), nav_history=render.get("nav_history"),
            sizing_data=bb.data.get("_sizing"), scoring_data=bb.data.get("_score_cache"),
            title_date=context["research_started_at"].split("T")[0])
    file_receipt(pdf_path, kind="PDF")
    delivery = build_weekly_delivery(bb, roots=[module.MODELS_DIR, module.REPORT_DIR])
    delivery.update(memo_id=store.memo_id, memo_sha256=sha256(memo.encode("utf-8")).hexdigest())
    path = str(base) + "_valuations.json"
    save_manifest(path, delivery)
    validate_delivery_manifest(delivery)
    preserve_delivery_bundle(store, bb, md_path=md_path, pdf_path=pdf_path,
        appendix_path=appendix_path, delivery=delivery,
        allowed_roots=[module.MODELS_DIR, module.REPORT_DIR, module.RESEARCH_NOTES_DIR])
    with store.db._conn() as conn:
        conn.execute("UPDATE memos SET full_markdown=?,pdf_path=?,dcf_files=? WHERE id=?",
                     (memo, str(pdf_path), json.dumps(delivery["attachments"]), store.memo_id))
    persist_usage_once(store, bb)
    # File recovery is allowed before missing decision/cost reconciliation;
    # SMTP is not allowed to bypass the normal analytical persistence gates.
    if store.get("decisions_finalized") is None:
        store.update(status="incomplete", analytical_status="complete", artifact_status="available",
                     delivery_status=state.get("delivery_status", "pending"), operational_status="blocked",
                     artifacts=store.get("artifact_bundle")["artifacts"],
                     delivery_manifest=path, phase="decisions_pending")
        return store.status()
    committee = state.get("committee_gaps")
    if send_email and isinstance(committee, dict) and not committee.get("automatic_email_allowed", False):
        # stessa difesa della run: il recupero non spedisce cio' che il comitato non ammette
        send_email = False
        store.update(email_blocked_reason="Email automatica non ammessa dal comitato (stato "
                     + str(committee.get("status")) + ")")
    sent = deliver_once(store, module, [str(pdf_path), *([appendix_path] if appendix_path else []), *delivery["attachments"]],
        body_extra=_recovered_email_body(bb, delivery, saved),
        delivery=delivery, send_email=send_email, acknowledge_uncertain=acknowledge_uncertain_email)
    record_email_outcome(delivery, sent)
    save_manifest(path, delivery)
    return finish_status(store, bb, pdf_path=pdf_path, md_path=md_path, appendix_path=appendix_path,
                         delivery=delivery, delivery_path=path, persistence_ok=True, sent=sent)
