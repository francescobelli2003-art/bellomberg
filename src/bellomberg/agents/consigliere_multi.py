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
from bellomberg.core.language import capture_language, language_context, scoped_language
from bellomberg.storage.memory_db import MemoryDB, RESEARCH_NOTES_DIR
from bellomberg.agents.action_validator import RESEARCH_GATE_POLICY, research_gate_enabled


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

# Fase D filing: attesa massima dell'aggiornamento pre-run, dall'AVVIO della run (non
# dall'avvio dell'aggiornamento). Oltre, la scheda usa l'ultimo confronto e lo dichiara.
FILING_ATTESA_MAX_S = 60


def _filing_service():
    """Servizio filing della run (monkeypatchabile nei test)."""
    from bellomberg.api.filing_routes import default_service
    return default_service()


# Aggiornamenti pre-run aperti: a fine run (o su crash) i lavori non partiti si annullano.
_PRERUN_FILING = []


def _chiudi_prerun_filing():
    """Annulla i lavori pre-run in coda (cancel_futures); quelli in corso finiscono. Mai solleva."""
    while _PRERUN_FILING:
        agg = _PRERUN_FILING.pop()
        try:
            agg.chiudi()
        except Exception as exc:
            _log("  [!] Filing: chiusura dell'aggiornamento pre-run non riuscita: "
                 + type(exc).__name__ + ": " + str(exc)[:160])


def _memo_per_novita(db):
    """(memo recenti, None) oppure ([], motivo): memo illeggibili o archivio assente si dichiarano
    (log + contesto), mai «nessuna novità» zitta (REV_G2b B3)."""
    if not db:
        motivo = "archivio dei memo assente"
    else:
        try:
            return db.get_recent_memos(5), None
        except Exception as exc:
            motivo = f"memo illeggibili: {type(exc).__name__}: {exc}"[:200]
    _log("  [!] Filing: " + motivo + " -> NOVITÀ non determinabile")
    return [], motivo


def _con_novita(contesto, errore):
    """Contesto filing con la NOVITÀ n.d. dichiarata in testa quando i memo non si leggono."""
    if not errore:
        return contesto
    return "NOVITÀ: n.d. (" + errore + "): nessun titolo e' marcato come novità\n" + str(contesto)


def _filing_tickers(portfolio):
    return [p.get("ticker") for p in (portfolio or {}).get("positions", [])
            if isinstance(p, dict) and p.get("ticker")]


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


_ACTION_STATUSES = {"OPERATIVE", "BLOCKED", "OVERRIDE_PENDING", "CHECK_UNAVAILABLE"}
_BUY_ACTIONS = {"BUY", "ADD"}
# 04/10 (G6): azioni a cui nessun gate d'ingresso si applica; TUTTO il resto (BUY/ADD,
# azioni fuori elenco, righe illeggibili) e' operativo solo con un esito OPERATIVE.
_NON_ENTRY_ACTIONS = {"SELL", "TRIM", "HOLD", "RESEARCH", "HEDGE", "WATCH"}


def _action_cell_text(value):
    """Strip Markdown decoration for matching an assessment to its source row."""
    import re
    text = str(value or "").strip()
    text = re.sub(r"^\s*[*_`]+|[*_`]+\s*$", "", text)
    return text.strip()


def _action_ticker_symbol(value):
    """Keep the source symbol while dropping cell formatting/company suffixes."""
    import re
    cell = _action_cell_text(value)
    link = re.match(r"^\[([^\]]+)\]\([^)]*\)", cell)
    if link:
        cell = link.group(1).strip()
    match = re.match(r"([A-Za-z0-9^][A-Za-z0-9.^=_-]{0,19})", cell)
    return match.group(1) if match else cell


def build_publication_snapshot(memo_markdown, assessments, *, finalization_error=None,
                               source_markdown=None, withdrawal_warning=False):
    """Return a fail-closed Markdown projection and its matching action snapshot.

    The supplied memo is never mutated. A row stays in the published ACTION TABLE
    only when a row-index, action and ticker matched assessment says OPERATIVE
    and every persistence/apply step succeeded; TRIM/SELL/HOLD/RESEARCH/HEDGE/
    WATCH without an assessment stay (no entry gate applies). The caller retains
    the untouched Capo text in ``source_markdown`` (raw sidecar).

    04/10 (G6, review 04 G1/G5): rows come from the single ACTION TABLE parser
    (action_table_extract), the same one that fed the gate and the register. An
    unreadable header removes the whole table from the operative view and lists
    its lines as CHECK_UNAVAILABLE; OPERATIVE rows without a valuation model are
    listed under «PROPOSTE OPERATIVE SENZA VALUTAZIONE» (decisione PM 04/10).
    """
    import re
    from bellomberg.agents.action_table_extract import parse_action_table_rows, _split_markdown_row

    source = str(memo_markdown or "")
    assessments = list(assessments or [])
    by_index = {}
    for item in assessments:
        if isinstance(item, dict):
            try:
                by_index[int(item.get("row_index"))] = dict(item)
            except (TypeError, ValueError):
                continue

    lines = source.splitlines()
    parsed = parse_action_table_rows(source)
    heading_index = (next((i for i, line in enumerate(lines)
                           if re.match(r"^\s*##\s*ACTION\s+TABLE\b", line, re.I)), None)
                     if parsed.get("table_found") else None)
    table_lines = parsed.get("table_lines")
    parse_error = parsed.get("error")

    safe_assessments = []
    removed_lines = set()
    replaced_lines = {}
    nonoperative = []
    senza_valutazione = []
    columns = parsed.get("columns") or {}
    for row in parsed.get("rows") or []:
        row_index = row["row_index"]
        action = row["action"]
        ticker = row["ticker"]
        assessment = by_index.get(row_index)
        valid = bool(assessment)
        if valid:
            valid = (_action_ticker_symbol(assessment.get("ticker", "")).upper() == ticker.upper()
                     and _action_cell_text(assessment.get("action", "")).upper() == action
                     and assessment.get("status") in _ACTION_STATUSES)
        if not valid and action in _NON_ENTRY_ACTIONS and ticker:
            assessment = {
                "row_index": row_index, "action": action, "ticker": ticker,
                "eur": None, "status": "OPERATIVE",
                "reason": "Nessun gate d'ingresso applicabile.",
                "override_rationale": None,
            }
        elif not valid:
            assessment = {
                "row_index": row_index, "action": action, "ticker": ticker,
                "eur": None, "status": "CHECK_UNAVAILABLE",
                "reason": "Esito del gate non disponibile o non allineato alla riga sorgente.",
                "override_rationale": None,
            }
        else:
            assessment = dict(assessment)
            assessment["row_index"] = row_index
            assessment["action"] = action
            assessment["ticker"] = ticker

        status = assessment.get("status")
        if action not in _NON_ENTRY_ACTIONS and finalization_error and status == "OPERATIVE":
            assessment["status"] = status = "CHECK_UNAVAILABLE"
            assessment["reason"] = str(finalization_error)
            assessment["override_rationale"] = None
            assessment["senza_valutazione"] = False

        assessment.setdefault("reason", "Motivo non disponibile.")
        assessment.setdefault("override_rationale", None)
        safe_assessments.append(assessment)
        amount = assessment.get("eur")
        if amount is None:
            amount = row.get("size_raw")
        if status != "OPERATIVE":
            removed_lines.add(row["line_index"])
            nonoperative.append({
                "action": action, "ticker": ticker, "eur": amount,
                "status": status, "reason": str(assessment.get("reason") or "Motivo non disponibile."),
                "override_rationale": str(assessment.get("override_rationale") or ""),
            })
        elif assessment.get("senza_valutazione"):
            senza_valutazione.append({"action": action, "ticker": ticker, "eur": amount,
                                      "reason": str(assessment.get("reason") or "")})
            # REV R4 (decisione PM): l'etichetta sta nella RIGA del pannello operativo,
            # nella cella Timing (larga nel PDF) o, senza Timing, accanto al ticker
            cells = _split_markdown_row(lines[row["line_index"]])
            col = columns.get("timing", columns.get("ticker"))
            if col is not None:
                cells += [""] * (col + 1 - len(cells))
                label = "**SENZA VALUTAZIONE**"
                if col == columns.get("timing"):
                    cells[col] = label + (" — " + cells[col] if cells[col] else "")
                else:
                    cells[col] = (cells[col] + " " + label).strip()
                replaced_lines[row["line_index"]] = "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"

    def _declare_line(i, reason):
        cells = _split_markdown_row(lines[i])
        item = {"action": _action_cell_text(cells[0]).upper() if cells else "",
                "ticker": _action_ticker_symbol(cells[1]) if len(cells) > 1 else "",
                "eur": _action_cell_text(cells[2]) if len(cells) > 2 else None,
                "status": "CHECK_UNAVAILABLE", "reason": reason, "override_rationale": ""}
        nonoperative.append(item)
        # REV R2/R3: anche l'email (corpo_azioni legge gli esiti) elenca queste righe; senza
        # row_index non diventano decisioni.
        safe_assessments.append(dict(item, row_index=None, override_rationale=None))

    def _is_separator(i):
        cells = [c for c in _split_markdown_row(lines[i]) if c]
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells)

    if heading_index is not None:
        # Every `|` line of the ACTION TABLE section that is NOT a row the gate read
        # (unreadable header, second table, table split by a blank line) leaves the
        # operative view and is declared «non valutata» (REV R3): memo and PDFs show
        # exactly the rows the gate assessed.
        section_end = next((i for i in range(heading_index + 1, len(lines))
                            if re.match(r"^##\s+", lines[i])), len(lines))
        in_table = (set(range(table_lines[0], table_lines[1]))
                    if table_lines and not parse_error else set())
        # REV2 N3: una seconda intestazione «## ACTION TABLE …» piu' avanti non e' letta dal
        # parser: le sue righe valgono come le altre tabelle non valutate
        scan = list(range(heading_index + 1, section_end))
        for other in (j for j in range(section_end, len(lines))
                      if re.match(r"^\s*##\s*ACTION\s+TABLE\b", lines[j], re.I)):
            other_end = next((k for k in range(other + 1, len(lines)) if re.match(r"^##\s+", lines[k])), len(lines))
            scan += list(range(other + 1, other_end))
        for i in scan:
            section_end_i = next((k for k in range(i, len(lines)) if re.match(r"^##\s+", lines[k])), len(lines))
            if i in in_table or not lines[i].strip().startswith("|"):
                continue
            removed_lines.add(i)
            if _is_separator(i) or (i + 1 < section_end_i and lines[i + 1].strip().startswith("|")
                                    and _is_separator(i + 1)):
                continue      # separator or header line of a table: nothing to declare
            _declare_line(i, str(parse_error) if (parse_error and table_lines
                                                  and table_lines[0] <= i < table_lines[1])
                          else "riga fuori dalla tabella delle proposte (seconda tabella o tabella "
                               "spezzata da una riga vuota): non valutata dal gate")

    rendered = [replaced_lines.get(i, line) for i, line in enumerate(lines) if i not in removed_lines]

    def _table_line(vals):
        return "| " + " | ".join(str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")
                                 for v in vals) + " |"

    advisory = (
        "## Stato operativo\n\n"
        "Il testo analitico seguente conserva la valutazione originale del Capo. "
        "Le sole righe che restano nella ACTION TABLE sono operative; "
        "eventuali richiami nel testo discorsivo non costituiscono istruzioni d'esecuzione; "
        "le altre proposte sono riportate separatamente.\n"
    )
    if senza_valutazione:
        rows = [
            "## PROPOSTE OPERATIVE SENZA VALUTAZIONE",
            "",
            "| Azione | Ticker | EUR | Motivo |",
            "|---|---|---:|---|",
        ]
        rows += [_table_line([item["action"], item["ticker"], item["eur"], item["reason"]])
                 for item in senza_valutazione]
        advisory += "\n" + "\n".join(rows) + "\n"
    if nonoperative:
        rows = [
            "## PROPOSTE NON OPERATIVE",
            "",
            "| Azione | Ticker | EUR | Stato | Motivo | Deroga dichiarata |",
            "|---|---|---:|---|---|---|",
        ]
        rows += [_table_line([item["action"], item["ticker"], item["eur"], item["status"],
                              item["reason"], item["override_rationale"]]) for item in nonoperative]
        advisory += "\n" + "\n".join(rows) + "\n"

    projected = "\n".join(rendered)
    if heading_index is not None:
        # Put the notice before the ACTION TABLE so both PDF renderers keep it close
        # to the operational panel.
        action_pos = next((m.start() for m in re.finditer(r"(?m)^\s*##\s*ACTION\s+TABLE\b", projected, re.I)), 0)
        projected = projected[:action_pos] + advisory + "\n" + projected[action_pos:]
    else:
        projected = advisory + "\n" + projected
    if withdrawal_warning and any(item.get('action') in ('BUY', 'ADD') for item in nonoperative):
        warning = ('**GATE DI PUBBLICAZIONE: proposte BUY/ADD ritirate dalla tabella operativa. '
                   'Le proposte BUY/ADD elencate in PROPOSTE NON OPERATIVE restano non eseguibili '
                   'anche se richiamate nel testo; li sono dichiarati motivi e controlli mancanti.**')
        bluf = re.search(r'(?im)^##+\s*BLUF[^\n]*\n', projected)
        if bluf:
            projected = projected[:bluf.end()] + warning + '\n\n' + projected[bluf.end():]
        else:
            projected = '## BLUF\n' + warning + '\n\n' + projected
    return {
        "source_markdown": str(source_markdown if source_markdown is not None else source),
        "memo_markdown": projected,
        "assessments": safe_assessments,
        "nonoperative": nonoperative,
        "senza_valutazione": senza_valutazione,
        "finalization_error": str(finalization_error) if finalization_error else None,
    }


def _publication_db_assessments(assessments, source_rows):
    """DB identity of each assessment: the parser's actual source ticker token."""
    source_by_index = {
        int(row.get("row_index", index)): row
        for index, row in enumerate(source_rows or [])
        if isinstance(row, dict)
    }
    out = []
    for assessment in assessments or []:
        db_assessment = dict(assessment)
        source_row = source_by_index.get(assessment.get("row_index"))
        if source_row:
            source_ticker = str(source_row.get("ticker") or "").strip()
            source_action = str(source_row.get("action") or "").strip().upper()
            if (_action_ticker_symbol(assessment.get("ticker")) == source_ticker
                    and _action_cell_text(assessment.get("action")).upper() == source_action):
                # The persisted identity is the parser's actual source ticker token;
                # original cell spelling stays in decisions.proposal_ticker_cell/raw sidecar.
                db_assessment["ticker"] = source_ticker
                db_assessment["action"] = source_action
        out.append(db_assessment)
    return out


def _publication_hard_pairs(db_assessments, sanity_records, sanity_pairs):
    """BUY/ADD rows closed by a sanity BLOCK (assessment records + legacy memo pairs)."""
    blocked_tickers = {
        str(item.get("ticker") or "").strip().upper()
        for item in (sanity_records or [])
        if isinstance(item, dict) and str(item.get("severity") or "").upper() == "BLOCK"
    }
    legacy_hard_pairs = {
        (str(action or "").upper(), _action_ticker_symbol(ticker))
        for action, ticker in (sanity_pairs or [])
    }
    return list(dict.fromkeys(
        (str(item.get("action") or "").upper(), str(item.get("ticker") or ""))
        for item in db_assessments
        if str(item.get("action") or "").upper() in _BUY_ACTIONS
        and (str(item.get("ticker") or "").upper() in blocked_tickers
             or (str(item.get("action") or "").upper(), str(item.get("ticker") or "")) in legacy_hard_pairs)
    ))


_SEVERITA_ESITO = {"OPERATIVE": 0, "OVERRIDE_PENDING": 1, "CHECK_UNAVAILABLE": 2, "BLOCKED": 3}
_RITENTATIVI_TRANSITORI = 3


def _retry_transient(fn, *args, **kwargs):
    """REV2 N1: un guasto TRANSITORIO del registro («database is locked/busy») si ritenta
    prima di chiudere o declassare qualsiasi cosa. Ogni altro errore passa subito."""
    import sqlite3
    for attempt in range(_RITENTATIVI_TRANSITORI):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            text_ = str(exc).lower()
            if attempt == _RITENTATIVI_TRANSITORI - 1 or not ("locked" in text_ or "busy" in text_):
                raise
            _log("[!] registro occupato (" + str(exc)[:80] + "): nuovo tentativo " + str(attempt + 2)
                 + "/" + str(_RITENTATIVI_TRANSITORI))
            time.sleep(0.2 * (attempt + 1))


def _recorded_assessments(db, decision_ids):
    """{row_index: esito} GIA' scritto nel registro per le decisioni di questa pubblicazione.

    04/10 (G6, REV R1): l'esito registrato e' immutabile; ripresa e retry pubblicano da
    QUI (prima scrittura vince) invece di ricalcolarlo e sbattere sull'immutabilita'."""
    ids = [int(i) for i in decision_ids or []]
    if not ids:
        return {}
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id, proposal_row_index, proposal_action, action, proposal_ticker, ticker, eur_amount, "
            "assessment_status, assessment_reason, assessment_override_rationale, outcome_notes FROM decisions "
            "WHERE id IN (" + ",".join("?" * len(ids)) + ")", ids).fetchall()
    from bellomberg.storage.memory_db import MARCA_CHIUSA_DAL_GATE, MemoryDB
    out = {}
    for row in rows:
        (did, index, p_action, action, p_ticker, ticker, eur, status, reason, rationale, notes) = tuple(row)
        if index is None or status is None:
            continue
        if MARCA_CHIUSA_DAL_GATE in str(notes or ""):
            # REV2 N7: gia' ritirata dal gate in una pubblicazione precedente: vale lo
            # stato pubblicato allora, non l'OPERATIVE immutabile della prima scrittura
            status = MemoryDB._effective_assessment_status({"outcome_notes": notes, "assessment_status": status})
            reason = str(notes).split(MARCA_CHIUSA_DAL_GATE, 1)[1].strip().rstrip("] ")
        out[int(index)] = {"decision_id": did, "row_index": int(index),
                           "action": str(p_action or action or ""), "ticker": str(p_ticker or ticker or ""),
                           "eur": eur, "status": status, "reason": reason or "",
                           "override_rationale": rationale,
                           "senza_valutazione": status == "OPERATIVE"
                           and str(reason or "").startswith("SENZA VALUTAZIONE")}
    return out


def _persist_and_read(bb, db, store, memo_id, capo_source_memo, source_rows, db_assessments):
    """Decisioni + esiti dal testo intatto del Capo; ritorna (ids, esiti registrati).
    Parser deterministico (nessuna chiamata LLM); il replace settimanale e' atomico e
    idempotente per testo del memo, quindi una ripresa non duplica le proposte. Se gli
    esiti sono gia' registrati (ripresa, retry) NON si riscrivono: si rileggono."""
    from bellomberg.core.llm_client import request_scope
    at_usage = {}
    try:
        with request_scope(getattr(store, "request_journal", None),
                           phase="action_extraction", agent="_action_table"):
            prepared = db.extract_and_save_decisions(
                memo_id, capo_source_memo, usage_out=at_usage, raw_rows=source_rows,
                _prepare_only=True, _strict=True)
            decision_ids = db.replace_memo_decisions(
                memo_id, capo_source_memo, usage_out=at_usage, prepared_rows=prepared)
        _log("Decisions persisted from Capo source: " + str(len(decision_ids)))
        expected = {a.get("row_index") for a in db_assessments or []}
        recorded = _retry_transient(_recorded_assessments, db, decision_ids)
        if recorded:
            if set(recorded) != expected:
                raise RuntimeError("esiti registrati non allineati alle righe del memo: registrati "
                                   + str(sorted(recorded)) + ", attesi " + str(sorted(expected)))
            _log("Esiti gia' registrati per questo memo: pubblicazione dal registro (prima scrittura vince)")
            return decision_ids, recorded
        assessment_result = db.record_action_assessments(memo_id, db_assessments)
        if isinstance(assessment_result, dict) and assessment_result.get("recorded") is not None:
            if int(assessment_result["recorded"]) != len(db_assessments):
                raise RuntimeError("assessment persist count mismatch")
        return decision_ids, _retry_transient(_recorded_assessments, db, decision_ids)
    finally:
        _record_side_usage(bb, "_action_table", at_usage)


def _close_sanity_blocks(db, memo_id, hard_pairs):
    """Auto-chiusura (SKIPPED + AUTO-ESCLUSA) delle BUY/ADD registrate BLOCKED. Ritorna
    None o il guasto DICHIARATO (mai inghiottito: REV R1). L'esito BLOCKED e' gia' nel
    registro, quindi la proposta resta non eseguibile anche se la chiusura manca."""
    if not hard_pairs:
        return None
    from bellomberg.agents.action_validator import apply_sanity_exclusions, sanity_exclusions_not_closed
    try:
        n_applied = _retry_transient(apply_sanity_exclusions, db, memo_id, hard_pairs, strict=True)
        # 04/10 (G2): conta lo stato FINALE, non le righe toccate in questo giro: in
        # ripresa o al retry le decisioni sono gia' SKIPPED + AUTO-ESCLUSA.
        not_closed = sanity_exclusions_not_closed(db, memo_id, hard_pairs)
    except Exception as exc:
        return "auto-chiusura sanity BLOCK fallita: " + type(exc).__name__ + ": " + str(exc)[:160]
    _log("Sanity BLOCK apply: " + str(n_applied) + " chiuse ora, "
         + str(len(hard_pairs) - len(not_closed)) + "/" + str(len(hard_pairs)) + " chiuse nel registro")
    if not_closed:
        return "sanity BLOCK non chiuse nel registro: " + str(not_closed)[:160]
    return None


def _recorded_hard_pairs(recorded):
    return list(dict.fromkeys((str(r["action"]).upper(), str(r["ticker"]))
                              for r in (recorded or {}).values()
                              if r.get("status") == "BLOCKED" and str(r["action"]).upper() in _BUY_ACTIONS))


def _persist_publication_decisions(bb, db, store, memo_id, capo_source_memo, source_rows,
                                   db_assessments, hard_pairs):
    """Source decisions + assessments + hard sanity closures, from the untouched Capo text.
    Idempotente (G2, REV R1). Un guasto dell'auto-chiusura qui SOLLEVA dichiarato."""
    decision_ids, recorded = _persist_and_read(bb, db, store, memo_id, capo_source_memo,
                                               source_rows, db_assessments)
    closure_error = _close_sanity_blocks(db, memo_id, hard_pairs)
    if closure_error:
        raise RuntimeError(closure_error)
    return decision_ids


def _reconcile_register(db, recorded, published):
    """REV R1 / REV2 N1: il registro non puo' essere piu' permissivo del memo pubblicato.
    Una riga registrata OPERATIVE che il memo dichiara non operativa viene chiusa SKIPPED
    DAL GATE (actor 'publication_gate', mai il PM: close_decision_by_publication_gate),
    e il suo stato effettivo diventa quello pubblicato. Ritorna {row_index: decision_id}."""
    withdrawn = {}
    for index, rec in (recorded or {}).items():
        pub = published.get(index) or {}
        if rec.get("status") != "OPERATIVE" or pub.get("status") == "OPERATIVE":
            continue
        if db.close_decision_by_publication_gate(rec["decision_id"], pub.get("status"), pub.get("reason")):
            withdrawn[index] = rec["decision_id"]
    if withdrawn:
        _log("[!] Registro riallineato al memo pubblicato (chiusura del gate, non del PM): "
             + str(sorted(withdrawn.values())))
    return withdrawn


_NOTA_RITIRO = ("; chiusa SKIPPED nel registro dal gate (non dal PM): via d'uscita la "
                "divergenza manuale se viene eseguita")


def _annota_esiti(assessments, by_index, nota):
    for assessment in assessments or []:
        if assessment.get("row_index") in by_index and nota not in str(assessment.get("reason") or ""):
            assessment["reason"] = str(assessment.get("reason") or "") + nota


def _finalize_publication_decisions(bb, db, store, memo_id, capo_source_memo, publication, decision_ids):
    """Retry della finalizzazione (decisions_error dal gate). Idempotente e mai bloccante
    per sempre (REV R1/R2): pubblica gia' congelata = verita'; il registro si riallinea a
    lei; ogni guasto residuo torna DICHIARATO e la consegna procede (il memo dichiara gia'
    CHECK_UNAVAILABLE le righe che il gate non ha potuto confermare)."""
    from bellomberg.agents.action_table_extract import parse_action_table_rows
    errors = []
    published = {a.get("row_index"): a for a in publication.get("assessments") or []
                 if isinstance(a, dict) and isinstance(a.get("row_index"), int)}
    recorded = {}
    try:
        rows = list((parse_action_table_rows(capo_source_memo) or {}).get("rows") or [])
        rows, db_assessments = _persistable(
            rows, _publication_db_assessments(publication.get("assessments") or [], rows))
        decision_ids, recorded = _persist_and_read(bb, db, store, memo_id, capo_source_memo,
                                                   rows, db_assessments)
    except Exception as exc:
        errors.append("decisioni/esiti non persistiti: " + type(exc).__name__ + ": " + str(exc)[:160])
    try:
        withdrawn = _reconcile_register(db, recorded, published)
        # il memo e' congelato: lo dice l'email (corpo_azioni legge questi esiti)
        _annota_esiti(publication.get("assessments"), withdrawn, _NOTA_RITIRO)
    except Exception as exc:
        errors.append("riallineamento registro/memo fallito: " + type(exc).__name__ + ": " + str(exc)[:160])
    hard_pairs = _recorded_hard_pairs(recorded)
    closure_error = _close_sanity_blocks(db, memo_id, hard_pairs)
    if closure_error:
        errors.append(closure_error)
        # REV2 N5: l'errore arriva nell'email, riga per riga
        _annota_esiti(publication.get("assessments"),
                      {i for i, r in recorded.items() if (str(r["action"]).upper(), str(r["ticker"])) in hard_pairs},
                      "; auto-chiusura nel registro non riuscita (" + closure_error[:120]
                      + "): resta PENDING ma non eseguibile")
    try:
        _link_publication_decisions(bb, db, memo_id)
    except Exception as exc:
        errors.append("collegamento snapshot valutazione/decisione non salvato: " + str(exc)[:160])
    return list(decision_ids or []), ("; ".join(errors) or None)


def _persistable(source_rows, db_assessments):
    """Rows (and their assessments) that can become decisions: action AND ticker read.
    An unreadable row is declared CHECK_UNAVAILABLE in the publication, never a
    decision with an empty action/ticker (04/10, G6)."""
    rows = [r for r in source_rows or [] if str(r.get("action") or "").strip() and str(r.get("ticker") or "").strip()]
    indices = {int(r.get("row_index", i)) for i, r in enumerate(rows)}
    return rows, [a for a in db_assessments or [] if a.get("row_index") in indices]


def _research_publication_checks(bb, store, memo_markdown=None):
    """Availability from this run only. Never equate a seal with economic approval."""
    from bellomberg.core.research_analysis import (research_reference, _binding_seal_reference,
                                                   build_research_action_bindings)
    checks = {'row_thesis_evidence_binding': {'status': 'NOT_YET_ASSESSED',
               'source': 'Provenance binding follows the exact Capo ACTION TABLE; not semantic approval'},
              'run_identity': {'run_id': store.run_id, 'cutoff': store.context['research_started_at']}}
    try:
        saved = store.get('research_action_bindings')
        # A recovery uses the hashed checkpoints; no new live source lookup/AI.
        reference = (_binding_seal_reference(store.get('research_dossier')) if saved is not None
                     else research_reference(bb))
        checks['research_seal'] = {'status': 'AVAILABLE', 'source': reference}
        if memo_markdown is not None:
            if saved is None:
                saved = build_research_action_bindings(bb.data['_research_thesis'],
                    run_id=store.run_id, memo_markdown=memo_markdown,
                    cutoff=store.context['research_started_at'])
                store.complete('research_action_bindings', saved)
            checks['row_thesis_evidence_binding'] = saved
    except Exception as exc:
        checks['research_seal'] = {'status': 'CHECK_UNAVAILABLE', 'source': type(exc).__name__}
    synthesis = store.get('synthesis_context') or {}
    for key, value in [('risk', synthesis.get('risk_data')), ('sizing', bb.data.get('_sizing'))]:
        checks[key] = {'status': 'AVAILABLE' if isinstance(value, dict) and value and not value.get('error')
                       else 'CHECK_UNAVAILABLE', 'source': 'synthesis_context of this run'}
    checks['mandate'] = {'status': 'AVAILABLE' if store.context.get('mandate_sha256') else 'CHECK_UNAVAILABLE',
                         'source': 'run context mandate_sha256 (identity, not row compliance)'}
    return checks


def _publication_gate(bb, db, store, memo_id, memo, capo_source_memo, raw_sidecar_error, sanity_pairs):
    """Publication gate: source decisions, assessments, and hard sanity closures are
    committed before either PDF renderer sees the memo; the returned projection IS
    the memo every later artifact carries. One function for the fresh run and for a
    resumed run whose memo_validated predates the gate (review 04 G7).

    REV R1: se il registro porta gia' gli esiti (ripresa dopo un crash), il memo si
    pubblica da quelli; un guasto dell'auto-chiusura e' `decisions_error` dichiarato,
    non un errore di pubblicazione (l'esito BLOCKED e' gia' nel registro).
    `sanity_pairs` (blocco legacy del validator) resta per firma: le chiusure seguono
    gli esiti REGISTRATI, non una seconda lettura dei sidecar.

    Returns {"publication", "decision_ids", "decisions_error", "hard_pairs"}."""
    from bellomberg.storage.memory_db import _parse_eur_amount
    publication_error = raw_sidecar_error
    assessments = []
    source_rows = []
    try:
        from bellomberg.agents.action_table_extract import parse_action_table_rows
        parsed = parse_action_table_rows(capo_source_memo)
        source_rows = list((parsed or {}).get("rows") or [])
        if (parsed or {}).get("error"):
            publication_error = publication_error or str(parsed["error"])
    except Exception as parse_error:
        publication_error = publication_error or (
            "parser della ACTION TABLE non disponibile: " + type(parse_error).__name__ + ": " + str(parse_error)[:120])

    try:
        from bellomberg.agents.action_validator import collect_sanity_exclusions, assess_action_table
        contract = (getattr(store, 'context', {}) or {}).get('contract') or {}
        research = research_gate_enabled(contract)
        checks = _research_publication_checks(bb, store, capo_source_memo) if research else None
        sanity_records = collect_sanity_exclusions(capo_source_memo, report_dir=REPORT_DIR,
                                                  publication_contract=contract)
        assessments = assess_action_table(
            capo_source_memo, bb.data.get("_sizing"), sanity_exclusions=sanity_records,
            publication_contract=contract, research_checks=checks)
    except Exception as assessment_error:
        _log("[!] Action assessment unavailable: " + str(assessment_error))
        assessments = []
        for idx, row in enumerate(source_rows):
            act = str(row.get("action") or "").strip()
            entry = act.upper() not in _NON_ENTRY_ACTIONS
            assessments.append({
                "row_index": int(row.get("row_index", idx)), "action": act,
                "ticker": str(row.get("ticker") or "").strip(),
                "eur": _parse_eur_amount(row.get("size_raw")),
                "status": "CHECK_UNAVAILABLE" if entry else "OPERATIVE",
                "reason": "valutazione del gate non disponibile" if entry else "nessun gate d'ingresso applicabile",
                "override_rationale": None,
            })

    if publication_error:
        for assessment in assessments:
            if (str(assessment.get("action") or "").upper() not in _NON_ENTRY_ACTIONS
                    and assessment.get("status") == "OPERATIVE"):
                assessment["status"] = "CHECK_UNAVAILABLE"
                assessment["reason"] = publication_error
                assessment["override_rationale"] = None
                assessment["senza_valutazione"] = False

    persist_rows, db_assessments = _persistable(
        source_rows, _publication_db_assessments(assessments, source_rows))

    decision_ids = []
    decisions_error = None
    recorded = {}
    if db is None or memo_id is None:
        publication_error = publication_error or "registro decisioni non disponibile"
        decisions_error = publication_error
    else:
        try:
            decision_ids, recorded = _persist_and_read(
                bb, db, store, memo_id, capo_source_memo, persist_rows, db_assessments)
        except Exception as persist_error:
            publication_error = publication_error or (
                "persistenza decisioni/esiti fallita: " + type(persist_error).__name__ + ": " + str(persist_error)[:160])
            decisions_error = publication_error
            _log("[!] " + publication_error)
        for assessment in assessments:
            rec = recorded.get(assessment.get("row_index"))
            if rec is None:
                continue
            fresh = str(assessment.get("status"))
            if _SEVERITA_ESITO.get(fresh, 2) > _SEVERITA_ESITO.get(rec["status"], 2):
                # REV2 N2: «BUY su BLOCK mai operativa» vince sempre sulla prima scrittura:
                # il controllo ATTUALE e' piu' severo e si pubblica, con la causa
                assessment["reason"] = ("esito attuale " + fresh + " piu' severo di quello registrato ("
                                        + rec["status"] + "): " + str(assessment.get("reason") or ""))
                assessment["senza_valutazione"] = False
                _log("[!] riga " + str(rec["row_index"]) + " " + rec["ticker"] + ": " + assessment["reason"][:160])
                continue
            if rec["status"] != fresh:
                _log("[!] riga " + str(rec["row_index"]) + " " + rec["ticker"] + ": esito registrato "
                     + rec["status"] + " (prima scrittura) al posto del ricalcolo " + fresh)
            assessment.update(status=rec["status"], reason=rec["reason"],
                              override_rationale=rec["override_rationale"],
                              senza_valutazione=rec["senza_valutazione"])
        if decisions_error is None:
            hard_pairs = _recorded_hard_pairs(recorded)
            closure_error = _close_sanity_blocks(db, memo_id, hard_pairs)
            if closure_error:
                decisions_error = closure_error
                _log("[!] " + closure_error + " — esito BLOCKED gia' nel registro; nuovo tentativo in finalizzazione")
                # REV2 N5: dichiarato nel memo e nell'email, sulla riga
                _annota_esiti(assessments, {i for i, r in recorded.items()
                                            if (str(r["action"]).upper(), str(r["ticker"])) in hard_pairs},
                              "; auto-chiusura nel registro non riuscita (" + closure_error[:120]
                              + "): resta PENDING ma non eseguibile")
        if decisions_error is None:
            # Link the parsed source proposals to the reviewed valuation generation.
            try:
                _link_publication_decisions(bb, db, memo_id)
            except Exception as link_error:
                decisions_error = "collegamento snapshot valutazione/decisione non salvato: " + str(link_error)[:160]
                _log("[!] Valuation snapshot link skipped: " + str(link_error))

    publication = build_publication_snapshot(
        memo, assessments, finalization_error=publication_error,
        source_markdown=capo_source_memo, withdrawal_warning=research_gate_enabled(
            (getattr(store, 'context', {}) or {}).get('contract')))
    if recorded:
        # REV R1: il registro non resta piu' permissivo del memo (es. errore di
        # pubblicazione su righe gia' registrate OPERATIVE)
        try:
            withdrawn = _reconcile_register(db, recorded, {a.get("row_index"): a for a in publication["assessments"]
                                                           if isinstance(a.get("row_index"), int)})
            if withdrawn:
                # REV2 N1: memo ed email dicono che la proposta e' chiusa e perche'
                final_rows = [dict(a) for a in publication["assessments"] if isinstance(a.get("row_index"), int)]
                _annota_esiti(final_rows, withdrawn, _NOTA_RITIRO)
                publication = dict(build_publication_snapshot(memo, final_rows, finalization_error=None,
                                                              source_markdown=capo_source_memo,
                                                              withdrawal_warning=research_gate_enabled(
                                                                  (getattr(store, 'context', {}) or {}).get('contract'))),
                                   finalization_error=publication.get("finalization_error"))
        except Exception as reconcile_error:
            decisions_error = decisions_error or (
                "riallineamento registro/memo fallito: " + type(reconcile_error).__name__ + ": " + str(reconcile_error)[:160])
            _log("[!] " + decisions_error)
    return {"publication": publication, "decision_ids": decision_ids,
            "decisions_error": decisions_error, "hard_pairs": _recorded_hard_pairs(recorded)}


def _restore_validated(validated, capo_source_memo, run_gate):
    """memo_validated of a resumed run. A payload written by the gate restores the
    published snapshot as it was. A payload from before the gate (no `publication`)
    used to finalize with `ids: []` and lose the run's decisions in silence (review
    04 G7): the gate runs now on the untouched Capo source, declared in the log."""
    memo = validated["memo"]
    sanity_pairs = validated.get("sanity_pairs") or []
    if "publication" in validated:
        return {"memo": memo, "sanity_pairs": sanity_pairs,
                "publication": dict(validated.get("publication") or {"memo_markdown": memo, "assessments": []},
                                    source_markdown=capo_source_memo),
                "hard_pairs": [tuple(pair) for pair in (validated.get("hard_pairs") or [])],
                "decision_ids": list(validated.get("decision_ids") or []),
                "decisions_error": validated.get("decisions_error")}
    _log("[!] memo validato nel formato precedente al gate di pubblicazione: gate eseguito ora "
         "sulla sorgente del Capo (decisioni e esiti persistiti, memo proiettato)")
    gate = run_gate(memo, sanity_pairs)
    publication = dict(gate["publication"])
    return {"memo": publication["memo_markdown"], "sanity_pairs": sanity_pairs,
            "publication": publication, "hard_pairs": list(gate["hard_pairs"]),
            "decision_ids": list(gate["decision_ids"]), "decisions_error": gate["decisions_error"]}


def _link_publication_decisions(bb, db, memo_id):
    """Link newly extracted proposals to the exact generation reviewed by this committee."""
    with db._conn() as conn:
        candidates = conn.execute("SELECT id,ticker FROM decisions WHERE memo_id=?", (memo_id,)).fetchall()
    for decision_id, ticker in candidates:
        result = bb.valuation_results.get(str(ticker).upper())
        if result and result.get("snapshot_id"):
            db.link_valuation_snapshot(result["snapshot_id"], generation_id=result["generation_id"],
                                       decision_id=decision_id)


_MOTIVO_USCITA = {"testo": ""}   # letto dal gestore di crash del __main__: F4 vede il motivo, non «2»
_LAST_WEEKLY_OUTCOME = {}


def mandato_o_esci():
    """05/09 (criterio 5, spec §5): senza il MANDATO del PM il comitato NON parte — e lo dice
    PRIMA di costruire il blackboard e di pagare i desk, non al Capo dopo un'ora. Torna il
    mandato letto dal disco; assente/incompleto = messaggio con la causa e uscita 2."""
    from bellomberg.core import mandato_pm
    try:
        # 04/10 (G6): il comitato pretende anche i campi che lo schema lascia facoltativi
        # per Trade Idea e chat (tolleranza di sforo del sizing): senza, non parte e lo dice.
        return mandato_pm.richiedi_per_comitato(mandato_pm.carica())
    except mandato_pm.MandatoMancante as e:
        _log("MANDATO NON DICHIARATO: " + str(e))
        _log("Il comitato non parte senza il mandato del PM: compila la pagina Mandato e Diario (F18) e rilancia.")
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
    from bellomberg.core.research_analysis import is_research_mode, research_reference, research_digest
    selected_language = capture_language(getattr(blackboard, "language", None))
    _log("=" * 60)
    _log("ROUND " + str(round_n))
    _log("=" * 60)
    blackboard.current_round = round_n
    # seed della cache scorer PRIMA dei thread: cosi' i thread non mutano le
    # CHIAVI ESTERNE di blackboard.data (solo la sotto-dict, un nome ciascuno)
    blackboard.data.setdefault("_score_cache", {})

    def _run_one(SpClass):
        name = getattr(SpClass, "name", SpClass.__name__)
        check_blocked = getattr(blackboard, "raise_if_run_blocked", None)
        if callable(check_blocked):
            check_blocked()
        should_run = getattr(blackboard, "should_run_specialist", None)
        if callable(should_run) and not should_run(name, round_n):
            return
        try:
            trade_model_ref = None
            research_ref = research_reference(blackboard) if round_n == 2 and is_research_mode(blackboard) else None
            if (getattr(blackboard, "run_scope", "weekly") == "trade_idea"
                    and not is_research_mode(blackboard)):
                if round_n == 1 and name == "fundamentals":
                    blackboard.model_phase = "building"
                    blackboard.independent_round = 0
                if round_n == 2:
                    from bellomberg.agents.trade_idea import _verified_candidate_valuations
                    refs = _verified_candidate_valuations(blackboard)
                    if len(refs) != 1:
                        raise RuntimeError("Exact usable workbook missing from final desk context")
                    trade_model_ref = {key: refs[0][key] for key in
                                       ("snapshot_id", "generation_id", "workbook_sha256")}
            with language_context(selected_language):
                sp = SpClass(blackboard)
                report = sp.run(round_n)
            if research_ref is not None:
                from bellomberg.agents.capo import _e_segnaposto
                if (research_reference(blackboard) != research_ref
                        or getattr(sp, 'run_result_status', None) != 'complete'
                        or not isinstance(report, str) or not report.strip()
                        or _e_segnaposto(report) or blackboard.read(name, 2) != report):
                    raise RuntimeError('Desk final report did not discuss the supplied sealed research')
                blackboard.data.setdefault('_desk_research_reviews', {})[name] = {
                    'round': 2, 'research_ref': research_ref, 'report_sha256': research_digest(report)}
            if trade_model_ref is not None:
                from bellomberg.agents.capo import _e_segnaposto
                from bellomberg.agents.trade_idea import _plan_digest, _verified_candidate_valuations
                refs = _verified_candidate_valuations(blackboard)
                if (len(refs) != 1 or any(refs[0][key] != value for key, value in trade_model_ref.items())
                        or getattr(sp, "run_result_status", None) != "complete"
                        or _e_segnaposto(report) or blackboard.read(name, 2) != report):
                    raise RuntimeError("Desk final report did not discuss the supplied exact workbook")
                blackboard.data.setdefault("_desk_model_reviews", {})[name] = {
                    "round": 2, "model_ref": trade_model_ref, "report_sha256": _plan_digest(report)}
            completed = getattr(blackboard, "record_specialist_completion", None)
            if callable(completed):
                completed(name, round_n, report, getattr(sp, "run_result_status", None))
        except Exception as e:
            _log("[!] " + name + " round " + str(round_n) + " failed: " + str(e))
            failed = getattr(blackboard, "record_run_failure", None)
            if callable(failed):
                failed(e, desk=name, round_n=round_n)
            try:
                blackboard.write(name, round_n, "[ERROR]: " + str(e))
            except Exception:
                pass
        if callable(check_blocked):
            check_blocked()

    classes = _classes_for_round(round_n)
    if getattr(blackboard, "run_scope", "weekly") == "trade_idea" and round_n == 1:
        # All independently researched domains are available before the writer
        # starts its actual questions and authors the common economic plan.
        classes = [cls for cls in classes if cls.name != "fundamentals"] + [
            cls for cls in classes if cls.name == "fundamentals"]
    if getattr(blackboard, "run_scope", "weekly") == "trade_idea" and round_n == 2:
        addressed = getattr(blackboard, "r2_specialists", set())
        classes = [cls for cls in SPECIALIST_ORDER if cls.name in addressed]
    if round_n == 2:
        # R2 SELETTIVO e SEQUENZIALE in ordine di pipeline (review 15/07): Quant deve
        # vedere la revisione R2 di Fundamentals e Options il verdetto R2 di Quant —
        # in parallelo un declassamento post-red-team non arriverebbe mai a valle.
        _log("R2 SELETTIVO in pipeline: " + " -> ".join(c.name for c in classes)
             + (" (repliche ai destinatari delle obiezioni)" if getattr(blackboard, "run_scope", "weekly") == "trade_idea"
                else " (macro/eventdesk/crypto chiudono in R1)"))
        for SpClass in classes:
            _run_one(SpClass)
        return

    workers = (1 if getattr(blackboard, "run_scope", "weekly") == "trade_idea"
               else _parallel_workers())
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


def _riga_usage_laterale(agent, entry):
    """Riga di log di una chiamata laterale (sonda, reflection, action table), letta dalla
    ENTRY normalizzata da record_usage — la stessa che va nel conto della run.

    05/10 sera (Opus 5.5, HANDOFF-4 voce 6): la riga leggeva usage["in"]/["out"], ma la
    sonda consegna lo schema del client (input_tokens/output_tokens) -> «in=None out=None»
    con i token veri nel journal; e "%.2f" mostrava «0,00 EUR» per costi sotto il
    centesimo (un ping costa frazioni di cent). Ora: token dalla entry, costo con
    format_eur (4 decimali sotto il cent) e ogni buco dichiarato n.d. COL MOTIVO
    (lo status della entry), mai uno 0,00 al posto del dato.
    """
    e = entry if isinstance(entry, dict) else {}
    # str(): uno status non stringa non deve far cadere la riga (l'except del chiamante
    # direbbe «usage non registrato» per una entry GIA' registrata).
    status = str(e.get("status") or "entry assente")
    mancanti = e.get("tokens_missing") if isinstance(e.get("tokens_missing"), list) else []

    def _tok(k):
        v = e.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            return str(v)
        # con status "ok" lo status non e' un motivo: il buco e' nei token non consegnati
        if status == "ok":
            if mancanti:
                return "n.d. (token non esposti, mancanti: " + ",".join(map(str, mancanti)) + ")"
            return "n.d. (token non esposti dal provider)"
        return "n.d. (" + status + ")"

    c = e.get("cost_eur")
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        if 0 < abs(c) < 0.0001:
            # R-36 F1: la sonda costa ~1e-6 EUR; format_eur (4 decimali) la stamperebbe
            # «0,0000 EUR», lo stesso zero finto di prima -> cifre significative.
            c_s = "< 0,0001 EUR (" + ("%.1e" % c).replace(".", ",") + ")"
        else:
            from bellomberg.core.llm_pricing import format_eur
            c_s = format_eur(c)
    else:
        c_s = "n.d. (" + status + ")"
    return (str(agent) + " usage: in=" + _tok("in") + " out=" + _tok("out")
            + " status=" + status + " costo=" + c_s)


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
        try:
            _log(_riga_usage_laterale(agent, entry))
        except Exception as e:
            # la entry e' GIA' registrata: fallisce solo la riga, e lo si dice cosi'
            _log("[!] " + str(agent) + " usage registrato, riga di log non formattata: "
                 + type(e).__name__)
        return entry
    except Exception as e:
        _log("[!] " + str(agent) + " usage non registrato (procedo): " + str(e))
        return None


def _collect_dcf_files(start_time, valuation_results=None):
    if valuation_results is not None:
        from bellomberg.reporting.valuation_delivery import build_manifest
        return build_manifest(valuation_results, roots=[MODELS_DIR, REPORT_DIR])["attachments"]
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


def _send_weekly_email(attachments, body_extra="", *, delivery=None,
                       expected_hashes=None, require_all_hashes=False):
    """Il booleano del mittente e' l'esito: False non e' un invio riuscito.
    body_extra: HTML con l'esito delle valutazioni e i modelli allegati (audit 11/09)."""
    if not email_configurata():
        _log("[!] Email not configured")
        return False
    _log("Sending Bellomberg email with all attachments")
    try:
        from bellomberg.reporting.email_sender import invia_email_multi_allegati
        sent = invia_email_multi_allegati(
            pdf_paths=attachments,
            oggetto="[BELLOMBERG] Weekly Research - " + datetime.now().strftime("%d/%m/%Y"),
            body_extra=body_extra,
            **({"expected_hashes": expected_hashes if expected_hashes is not None else delivery["expected_hashes"],
                "delivery_receipt": delivery, "require_all_hashes": require_all_hashes}
               if delivery is not None else {}),
        )
        if sent is True:
            _log("Email sent (" + str(len(attachments)) + " files)")
            return True
        _log("[!] Email NON inviata: mittente ha restituito esito negativo (vedi log SMTP/allegati)")
    except Exception as e:
        _log("[!] Email error: " + str(e))
    return False


def _ensure_portfolio_valuations(blackboard, portfolio):
    from bellomberg.agents.company_research_tools import source_research_guard
    with source_research_guard(blackboard):
        return _ensure_portfolio_valuations_locked(blackboard, portfolio)


def _ensure_portfolio_valuations_locked(blackboard, portfolio):
    """Cover unrequested DB holdings after R1, without retrying any desk attempt."""
    from bellomberg.agents import chat_tools
    from bellomberg.agents.specialists.base import TOOL_LOG_OUTPUT_MAX

    positions = (portfolio or {}).get("positions") or []
    tickers = list(dict.fromkeys(p["ticker"].strip().upper() for p in positions
        if isinstance(p, dict) and isinstance(p.get("ticker"), str) and p["ticker"].strip()))
    seen = {str(t).strip().upper() for t in blackboard.valuation_results}
    seen.update(str(a.get("ticker") or "").strip().upper() for a in blackboard.valuation_attempts)
    coverage = {"portfolio_tickers": tickers, "already_requested": [t for t in tickers if t in seen],
                "requested": [], "preparation_unavailable": {}}
    blackboard.data["_valuation_coverage"] = coverage
    if not tickers:
        coverage.update(status="unavailable", reason="portfolio_empty_or_unavailable")
        _log("[KO] get_valuation: portafoglio vuoto/non disponibile; nessun titolo dedotto")
        return
    state = blackboard.data.get("_valuation_preparation") or {}
    for ticker in tickers:
        if ticker in seen:
            continue
        preparer = blackboard.valuation_preparer if state.get("status") == "enabled" else None
        reason = state.get("reason") or "preparer_unavailable"
        if state.get("tickers") and ticker not in state["tickers"]:
            preparer, reason = None, "ticker_not_authorized"
        if preparer is None:
            coverage["preparation_unavailable"][ticker] = reason
        _log("[committee-orchestrator] -> get_valuation(" + ticker + ")")
        blackboard.mark_specialist_start("committee-orchestrator", blackboard.current_round)
        try:
            result = chat_tools.dispatch("get_valuation", {"ticker": ticker},
                caller="committee-orchestrator", valuation_preparer=preparer)
            payload = result.get("data", result) if isinstance(result, dict) else result
            if not isinstance(payload, dict):
                raise ValueError("get_valuation returned invalid result object")
            usability = payload.get("valuation_usability")
            if usability is not None and (not isinstance(usability, dict)
                    or type(usability.get("usable")) is not bool):
                raise ValueError("get_valuation returned invalid valuation_usability")
        except Exception as exc:
            payload = {"ok": False, "error": type(exc).__name__ + ": " + str(exc),
                       "exclude_from_action_table": True}
        payload = {**payload, "request_origin": "committee-orchestrator"}
        usable = (payload.get("valuation_usability") or {}).get("usable") is True
        if preparer is None and not usable:
            payload["preparation"] = {"status": "disabled", "reason": reason}
            payload["error"] = (str(payload.get("error") or "Valutazione incompleta")
                                + "; preparazione automatica non disponibile: " + reason)
        coverage["requested"].append(ticker)
        output = json.dumps(payload, default=str, ensure_ascii=False)
        blackboard.tool_log.append({"specialist": "committee-orchestrator", "round": blackboard.current_round,
            "tool": "get_valuation", "input": str({"ticker": ticker}),
            "time": datetime.now().strftime("%H:%M:%S"), "output": output[:TOOL_LOG_OUTPUT_MAX],
            "output_tappato": len(output) > TOOL_LOG_OUTPUT_MAX, "output_chars": len(output)})
        blackboard.record_valuation(ticker, payload, "committee-orchestrator")
        blackboard.mark_specialist_done("committee-orchestrator")
        _log("[committee-orchestrator] " + ticker + ": "
             + ("calcolata" if usable
                else "KO - " + str(payload.get("error") or "valutazione incompleta")))
    coverage["status"] = "checked"


from bellomberg.agents.trade_idea import paid_run_exclusive


def _weekly_contract(*, analysis_mode='fundamentals_research_v1'):
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    if analysis_mode not in (None, RESEARCH_ANALYSIS_MODE):
        raise ValueError('Unknown weekly analysis mode')
    from bellomberg.core.llm_client import modello_o_buco
    models = {"capo": modello_o_buco("capo"), "red_team": modello_o_buco("red_team"),
              "reflection": modello_o_buco("reflection"), "action_extractor": modello_o_buco("action_extractor")}
    for cls in SPECIALIST_ORDER:
        for round_n in (0, 1, 2):
            models[cls.name + ":" + str(round_n)] = modello_o_buco("consigliere", cls.name, round_n)
    from bellomberg.core.mandato_pm import MANDATE_TEXT_POLICY, MANDATE_TEXT_POLICY_KEY
    from datetime import datetime, timezone
    from bellomberg.core.evidence_followup_policy import KEY, POLICY, AS_OF_KEY
    contract = {MANDATE_TEXT_POLICY_KEY: MANDATE_TEXT_POLICY, "models": models, "roster": [cls.name for cls in SPECIALIST_ORDER],
                KEY: POLICY, AS_OF_KEY: datetime.now(timezone.utc).date().isoformat(),
                "r1_stages": R1_STAGES, "r2_specialists": sorted(R2_SPECIALISTS),
                "reflection_policy": "weekly-reflection36/1",
                "research_notes_policy": "weekly-research-notes/1",
                "memo_facts_policy": "weekly-facts/1",
                "semantic_memory_policy": "weekly-published-chunks/1",
                "source_health_policy": "weekly-source-health/1",
                "quant_render_policy": "weekly-quant-snapshot/1", "evidence_prompt_policy": "weekly-evidence-prompts/1"}
    if analysis_mode is not None:
        contract['analysis_mode'] = analysis_mode
        contract['publication_gate_policy'] = RESEARCH_GATE_POLICY
    return contract


def _capture_publication_natures(contract):
    """Freeze existing nature exceptions once, before creating a new research run."""
    if not research_gate_enabled(contract):
        return contract
    from bellomberg.storage.classificazione import carica_veicoli, natura
    contract = dict(contract)
    try:
        store = carica_veicoli()
        contract['instrument_natures'] = {ticker: natura(ticker, store).as_dict()
                                         for ticker in store['veicoli']}
        contract['instrument_natures_source'] = {'origin': store['origine'], 'reason': store.get('motivo')}
    except Exception as exc:
        contract['instrument_natures'] = {}
        contract['instrument_natures_source'] = {'status': 'CHECK_UNAVAILABLE', 'reason': type(exc).__name__}
    return contract


def _resume_publication_contract(contract, saved):
    """Compare current models, while preserving historical gate policy and natures."""
    from bellomberg.core.reflection_policy import enabled as _reflection_enabled
    _reflection_enabled(saved)
    research_gate_enabled(saved)
    from bellomberg.core.current_facts import research_notes_enabled
    research_notes_enabled(saved)
    from bellomberg.core.mandato_pm import text_policy_from_context
    text_policy_from_context(saved)
    from bellomberg.core.evidence_followup_policy import enabled as _followup_enabled, frozen_as_of
    if _followup_enabled(saved):
        frozen_as_of(saved)
    contract = dict(contract)
    for key in ('publication_gate_policy', 'instrument_natures', 'instrument_natures_source',
                'memo_facts_policy', 'semantic_memory_policy', 'source_health_policy', 'quant_render_policy', 'mandate_text_policy', 'evidence_prompt_policy', 'reflection_policy', 'research_notes_policy',
                'evidence_followup_policy', 'evidence_followup_as_of'):
        if key in saved:
            contract[key] = saved[key]
        else:
            contract.pop(key, None)
    return contract


def _weekly_terminal_heartbeat(store):
    from pathlib import Path
    from bellomberg.agents.specialists.base import scrivi_file_atomico
    path = Path(Blackboard.HEARTBEAT_PATH)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    # Seguito V2 (PM 05/10, Opus 5.5): il registro si fonde SOPRA l'ultimo heartbeat della
    # Blackboard. Se la run e' morta prima di crearla, quel file e' della run PRECEDENTE:
    # tool_log, usage, start_time e technical_status suoi non si spacciano per questa run.
    # Si scartano e lo si dichiara (`lavagna` + `heartbeat_precedente_scartato`). Questa run =
    # stesso run_id, oppure run_id ancora None (la Blackboard nasce senza, bind_blackboard lo
    # mette dopo) con lo stesso memo.
    stessa = bool(state) and (state.get("run_id") == store.run_id
                              or (state.get("run_id") is None and state.get("memo_id") == store.memo_id))
    if state and not stessa:
        state = {"heartbeat_precedente_scartato": {k: state.get(k) for k in ("run_id", "memo_id", "start_time")}}
    state["lavagna"] = "questa_run" if stessa else "assente"
    result = store.status()
    state.update(result, run_scope="weekly", running=False,
                 message=((result.get("last_error") or result.get("first_error") or {}).get("message")
                          if result["status"] != "completed" else "Analisi e artefatti verificati"))
    ok, error = scrivi_file_atomico(str(path), json.dumps(state, ensure_ascii=False, default=str))
    if not ok:
        _log("[!] Stato finale non scritto nel heartbeat: " + str(error))


def _calcola_guardrail_beta():
    """Guardrail beta (advanced_metrics.reconcile_betas) calcolato UNA volta per run, nel
    priming (PM 06/10): lo leggono il punteggio quant in R1 e il blocco del Capo. Un guasto
    non sparisce: payload {"error": tipo} DICHIARATO, e a valle la beta e' esclusa/vietata.
    review R-SEG 06/10: `calcolato_il` nel payload (anche di errore), cosi' una ripresa in un
    altro giorno lo DICHIARA al Capo; riga di log PRIMA del calcolo (rete/DB senza timeout:
    un blocco non resta muto)."""
    calcolato_il = datetime.now().isoformat(timespec="seconds")
    _log("Guardrail beta: calcolo (3 motori: TWR, rischio, fattori)...")
    try:
        from bellomberg.portfolio.advanced_metrics import reconcile_betas
        rb = reconcile_betas()
    except Exception as e:
        _log("[!] guardrail beta non disponibile: " + type(e).__name__)
        return {"error": type(e).__name__, "calcolato_il": calcolato_il}
    if not isinstance(rb, dict):
        _log("[!] guardrail beta: payload non valido (" + type(rb).__name__ + ")")
        return {"error": "payload non valido", "calcolato_il": calcolato_il}
    rb = dict(rb, calcolato_il=calcolato_il)
    _log("Guardrail beta: " + str(rb.get("verdict") or "senza verdetto") + " " + str(rb.get("betas"))
         + " (calcolato il " + calcolato_il + ")")
    return rb


def _ferma_preriscaldamento():
    """FRESCHEZZA (07/10): a fine run il preriscaldamento si ferma (niente cache ne' log dopo la fine)."""
    try:
        from bellomberg.market_data.freschezza_trimestrale import ferma_preriscaldamenti
        vivi = ferma_preriscaldamenti()
        if vivi:
            _log("  Freschezza: preriscaldamento fermato a fine run (%d ancora in corso)" % vivi)
    except Exception as exc:
        _log("  [!] Freschezza: arresto del preriscaldamento non riuscito: " + type(exc).__name__)


@scoped_language
@paid_run_exclusive
def run_multi_agent(*, resume_memo_id=None, delivery_only=False,
                    authorize_new_ai=False, send_email=True, acknowledge_uncertain_email=False):
    """Normal run, explicit continuation, or artifact-only recovery of one memo."""
    from pathlib import Path
    from bellomberg.agents.weekly_lifecycle import create_run, validate_resume, recover_delivery, require_existing_database
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked, WeeklyRunStore, digest, costs_unresolved
    from bellomberg.core.request_journal import RequestJournal
    from bellomberg.core.llm_client import request_scope
    _LAST_WEEKLY_OUTCOME.clear()
    if delivery_only and resume_memo_id is None:
        raise WeeklyRunBlocked("Seleziona esplicitamente il memo da recuperare")
    if not delivery_only:
        # REV G7/R7 (04/10): effort non valido = run rifiutata PRIMA di nascere (create_run),
        # col nome della variabile; il controllo in _run_multi_agent resta come seconda rete.
        from bellomberg.core.llm_client import valida_effort_env
        valida_effort_env()
    from bellomberg.storage import memory_db as _memory_db
    require_existing_database(_memory_db.SQLITE_PATH)
    db = MemoryDB()  # A failed database is a hard prerequisite, before any tool/provider.
    if resume_memo_id is None:
        mandate = mandato_o_esci()
        portfolio = db.get_portfolio_summary()
        store = create_run(db, portfolio, mandate, _capture_publication_natures(_weekly_contract()), capture_language())
        from bellomberg.core.current_facts import freeze_research_notes
        freeze_research_notes(store, capture=True)
    else:
        store = WeeklyRunStore(db, resume_memo_id)
        if store.context['contract'].get('analysis_mode') is None:
            # Historical contracts remain unchanged. Continuing one must not
            # reactivate its author/preparer/compiler through the ordinary UI.
            if not delivery_only:
                raise WeeklyRunBlocked('Run legacy in archivio: consentito solo il recupero esplicito '
                    'della consegna gia persistita (delivery_only); nessuna nuova AI o generazione Excel')
            if store.get('memo_validated') is None:
                raise WeeklyRunBlocked('Memo validato assente: run legacy in archivio, '
                    'ripresa analitica ed Excel non autorizzati')
    # Validate accepted policy markers before checking individual new checkpoints.
    # This also preserves the existing diagnostic when multiple prerequisites are broken.
    _resume_publication_contract(store.context['contract'], store.context['contract'])
    from bellomberg.core.current_facts import freeze_research_notes
    freeze_research_notes(store)  # Invalid markers/missing new checkpoints fail before any replay.
    selected = store.context["language"]
    try:
        with store.claim(mode="delivery" if delivery_only else "resume" if resume_memo_id else "new"):
            try:
                with language_context(selected):
                    if delivery_only:
                        result = recover_delivery(store, sys.modules[__name__], send_email=send_email,
                                                  acknowledge_uncertain_email=acknowledge_uncertain_email)
                    elif resume_memo_id is not None and store.get("decisions_finalized") is not None:
                        # Idempotent second continuation: verify/recover delivery without any AI.
                        result = recover_delivery(store, sys.modules[__name__], send_email=send_email,
                                                  acknowledge_uncertain_email=acknowledge_uncertain_email)
                    else:
                        if resume_memo_id is not None:
                            if not authorize_new_ai:
                                raise WeeklyRunBlocked("Ripresa analitica richiede autorizzazione esplicita per le fasi mancanti")
                            validate_resume(store, mandato_o_esci(),
                                _resume_publication_contract(
                                    _weekly_contract(analysis_mode=store.context['contract'].get('analysis_mode')),
                                    store.context['contract']))
                        journal_path = Path(db.db_path).with_name("weekly-" + store.run_id + "-requests.sqlite")
                        if store.status().get("request_journal_path") and not journal_path.is_file():
                            raise WeeklyRunBlocked("Registro richieste mancante: nessun nuovo invio autorizzabile")
                        store.update(request_journal_path=str(journal_path))
                        store.request_journal = RequestJournal(
                            journal_path,
                            run_id=store.run_id, authorization={"scope": "weekly", "memo_id": store.memo_id,
                                "context_sha256": digest(store.context), "authorized_usd": None})
                        if costs_unresolved(store.request_journal.summary()):
                            # REGOLA PM 05/10 (decisione main 06/10): costo incerto DICHIARATO, la
                            # ripresa prosegue. Nessun reinvio: la stessa richiesta incerta ha la
                            # stessa chiave e il registro la rifiuta (RequestBlocked, mai pagata due volte).
                            _log("[!] Ripresa con richieste/costi INCERTI nel registro: DICHIARATI (da "
                                 "riconciliare), la ripresa prosegue; nessuna richiesta incerta viene reinviata")
                        with request_scope(store.request_journal, phase="weekly"):
                            result = _run_multi_agent(store, db, send_email=send_email)
            except BaseException as exc:
                try:
                    if getattr(store, "blackboard", None) is not None:
                        store.save_snapshot(store.blackboard)
                    store.fail(exc)
                    if getattr(store, "request_journal", None) is not None:
                        store.update(request_costs=store.request_journal.summary())
                except Exception as state_error:
                    _log("[!] Persistenza errore run fallita: " + str(state_error))
                raise
    except BaseException:
        _chiudi_prerun_filing()  # run interrotta: nessun aggiornamento filing ancora in coda
        _ferma_preriscaldamento()
        _LAST_WEEKLY_OUTCOME.update(store.status())
        _weekly_terminal_heartbeat(store)
        raise
    _ferma_preriscaldamento()
    result = store.status()
    _weekly_terminal_heartbeat(store)
    _LAST_WEEKLY_OUTCOME.update(result)
    return result


def _run_multi_agent(store, db, *, send_email=True):
    from bellomberg.core.llm_client import request_scope, valida_effort_env
    # G7/M1 (04/10): TUTTE le variabili *_EFFORT validate qui, prima di qualunque chiamata
    # pagata (sonda, desk, red team, Capo): un valore vuoto o non valido ferma la run col
    # NOME della variabile, invece di emergere al Capo a desk gia' pagati.
    valida_effort_env()
    start_time = datetime.now()
    avvio_monotonic = time.monotonic()   # scadenza assoluta dell'aggiornamento filing pre-run
    print_banner()
    _mandato_run = mandato_o_esci()   # 05/09 (criterio 5): niente mandato = niente run, dichiarato subito
    # log dai valori VERI (doc-fix audit/21 App. C: diceva "All agents on
    # claude-opus-4-8" ma R0 e' Sonnet dal 15/07 — il log ora non puo' mentire)
    try:
        # 05/09 (ordine PM): i modelli vivono nel .env, uno per desk; qui la base R1/R2,
        # R0 e Capo, con «n.d. (VARIABILE assente)» al posto di un nome inventato.
        from bellomberg.core.llm_client import modello_o_buco as _mob
        _log(f"Starting weekly run | R1/R2 {_mob('consigliere')} | R0 {_mob('consigliere', round_n=0)}"
             f" | Capo {_mob('capo')} | memory-aware")
        # audit 11/09 (Fable 5.1): l'intestazione diceva il modello BASE (glm-5.3-flash) mentre
        # tre desk giravano su un override per desk (glm-5.3), proprio quelli usciti a 0 char.
        # Gli override si dichiarano qui, una volta, cosi' il log non mente sul modello.
        _base = {r: _mob("consigliere", round_n=r) for r in (0, 1, 2)}
        _override = []
        for _sp in SPECIALIST_ORDER:
            for _r in (0, 1, 2):
                try:
                    _m = _mob("consigliere", getattr(_sp, "name", None), _r)
                except Exception:
                    continue
                if _m != _base.get(_r):
                    _override.append(f"{getattr(_sp, 'name', '?')} R{_r}={_m}")
        if _override:
            _log("  Override modelli per desk: " + ", ".join(_override))
    except Exception:
        _log("Starting weekly run | memory-aware (model strings non importabili)")

    # Persistence, run identity and the original book have already been verified.
    portfolio = store.context["portfolio"]
    memo_id = store.memo_id
    macro = None

    # Fase D filing: profili scaduti aggiornati IN BACKGROUND mentre la run prosegue;
    # il passo filing (I-20) attende al più fino a avvio + FILING_ATTESA_MAX_S. Mai bloccante.
    # Solo se il priming (dove vive il passo I-20) non e' gia' persistito: una ripresa
    # oltre il priming non rilegge il contesto filing e non deve avviare download.
    _filing_agg = None
    if store.get("priming") is None:
        try:
            _tickers_filing = _filing_tickers(portfolio)
            if _tickers_filing:
                from bellomberg.market_data.filing_prerun import AggiornamentoPreRun
                _filing_agg = AggiornamentoPreRun(_filing_service(), _tickers_filing,
                                                  avvio_run=avvio_monotonic, attesa_max_s=FILING_ATTESA_MAX_S)
                _filing_agg.avvia()
                _PRERUN_FILING.append(_filing_agg)
                _log(f"  Filing: aggiornamento dei profili scaduti avviato (attesa max {FILING_ATTESA_MAX_S:g} s dall'avvio)")
        except Exception as exc:
            _filing_agg = None   # freschezza dall'archivio: la scheda dichiara NON AGGIORNATO dove serve
            _log("  [!] Filing: aggiornamento pre-run non avviato: " + type(exc).__name__ + ": " + str(exc)[:160])

    correlation_matrix = None

    # BLACKBOARD con DB + memo_id
    from bellomberg.core.research_analysis import is_research_mode, seal_research_thesis, research_reference
    from bellomberg.valuation.preparation_runtime import preparation_status_text
    if is_research_mode(store.context['contract']):
        preparation_binding = {'preparer': None, 'state': {'status': 'not_required',
            'analysis_mode': store.context['contract']['analysis_mode'],
            'reason': 'Company research produces memo/PDF without workbook preparation'}}
    else:
        from bellomberg.valuation.preparation_runtime import bind_installation_preparer
        preparation_binding = bind_installation_preparer("committee")
    bb = Blackboard(memory_db=db, memo_id=memo_id, valuation_preparer=preparation_binding["preparer"])
    bb.data["_valuation_preparation"] = preparation_binding["state"]
    _log('Analisi societaria documentata: pacchetto memo/PDF, Excel non previsto'
         if is_research_mode(store.context['contract']) else
         preparation_status_text(preparation_binding["state"], language=bb.language))
    # totale report atteso (ripipeline: R0+R1 tutti, R2 selettivo) -> heartbeat/UI
    bb.expected_reports = len(SPECIALIST_ORDER) * 2 + len(_classes_for_round(2))
    # chi replica in R2: per gli ALTRI il report R1 e' il finale e va persistito
    # come tale (v. Blackboard.write) — senza questo la memoria li perderebbe
    bb.r2_specialists = set(R2_SPECIALISTS)
    from bellomberg.agents.weekly_lifecycle import bind_blackboard
    bind_blackboard(bb, store)
    from bellomberg.agents.company_research_tools import bind_weekly_company_research
    bind_weekly_company_research(bb, store)
    bb.request_journal = store.request_journal
    from bellomberg.core.quant_render_snapshot import enabled as _quant_enabled, capture_once as _quant_capture, prepare_snapshot as _quant_prepare, entry as _quant_entry
    _quant_render_enabled = _quant_enabled(store)
    from bellomberg.core.reflection_policy import enabled as _reflection_enabled
    _reflection_enabled(store.context.get('contract', {}))
    _priming = store.get("priming")
    if _priming is None:
        try:
            macro = tool_get_macro_dashboard()
            _log("  Macro: " + str(len(macro.get("indicators", {}))) + " indicators")
        except Exception as exc:
            _log("  Macro non disponibile: " + str(exc))
            macro = None
        if portfolio.get("positions"):
            correlation_matrix = _try_correlation_matrix(portfolio["positions"])
        # I-20: fotografia read-only dell'archivio per i ticker realmente presenti nel DB.
        # Il contesto non e' un desk e non altera expected_reports o i round.
        try:
            from bellomberg.agents.filing_context import committee_filing_context, ultima_run_comitato
            _freschezza = None
            try:
                _freschezza = _filing_agg.esiti() if _filing_agg else None
            except Exception as exc:
                _log("  [!] Filing: esiti dell'aggiornamento non disponibili: " + type(exc).__name__ + ": " + str(exc)[:160])
            _memos, _memos_errore = _memo_per_novita(db)  # REV_G2b B3: illeggibili = NOVITÀ n.d.
            bb.data["_filing_context"] = _con_novita(committee_filing_context(
                _filing_tickers(portfolio), freschezza=_freschezza,
                novita_dopo=ultima_run_comitato(_memos, escludi_id=memo_id)), _memos_errore)
            _non_agg = [t for t, e in (_freschezza or {}).items() if e.get("stato") != "aggiornato"]
            _log(f"  Filing: contesto {len(bb.data['_filing_context'])} caratteri; "
                 f"non aggiornati: {', '.join(_non_agg) or 'nessuno'}")
        except Exception as exc:
            bb.data["_filing_context"] = ("ARCHIVIO FILING NON DISPONIBILE: "
                                          + type(exc).__name__ + ": " + str(exc)[:200])
        # FRESCHEZZA (07/10): preriscaldamento dell'ultimo periodo pubblicato dei titoli della run, in
        # background (4 worker): i desk leggono dalla cache. Non blocca e non fa mai fallire la run.
        try:
            from bellomberg.market_data.freschezza_trimestrale import preriscalda_in_background
            preriscalda_in_background(_filing_tickers(portfolio), as_of=bb.data.get("_data_cutoff"), log=_log)
        except Exception as exc:
            _log("  [!] Freschezza: preriscaldamento non avviato: " + type(exc).__name__)
        if is_research_mode(bb):
            # V1-PONTE: documenti gia' verificati dall'archivio Filing nel dossier, prima di R0 (no rete/AI).
            # Esiti del pre-run letti UNA volta (gli stessi del contesto Filing); guasto = lacuna dichiarata.
            try:
                from bellomberg.agents import ponte_filing_dossier as _ponte
                _esiti_letti = {"esiti": locals()["_freschezza"]} if "_freschezza" in locals() else {}
                _ponte.ammetti_archivio_filing(bb, store, aggiornamento=_filing_agg,
                                               servizio_factory=_filing_service, log=_log, **_esiti_letti)
            except Exception as exc:
                _log("  [!] Ponte Filing -> dossier non eseguito: " + type(exc).__name__)
                try:
                    _ponte.ponte_non_eseguito(bb, exc)
                except Exception as exc2:
                    # APERTO-NUOVI 07/10: mai piu' `pass` muto. Log col tipo e dichiarazione minima nel dossier.
                    _log("  [!] Ponte Filing: lacuna non registrata dal ponte (" + type(exc2).__name__
                         + "): dichiarazione minima nel dossier")
                    try:
                        bb.data[_ponte.CHIAVE_BOARD] = _ponte.ricevuta_minima(
                            list(getattr(bb, "research_tickers", ()) or ()),
                            "ponte non eseguito: " + type(exc).__name__ + "; lacuna registrata in forma minima ("
                            + type(exc2).__name__ + ")")
                    except Exception as exc3:
                        _log("  [!] Ponte Filing: nemmeno la dichiarazione minima e' stata scritta ("
                             + type(exc3).__name__ + "): dossier senza esito del ponte")

        # HEALTH-CHECK PRE-RUN (audit/07 §3, P1): il giorno del memo #42 var_contribution
        # rispondeva "insufficient history: 0 obs" e la run e' partita comunque, senza
        # decomposizione del rischio e senza che nessuno lo sapesse. Ora i tool chiave
        # vengono pingati PRIMA dei round; i KO finiscono nel log E nel prompt del Capo
        # (stesso pattern del guardrail beta / guardie Polymarket).
        tool_health = {"ok": [], "ko": []}
        from bellomberg.core.source_health import capture_health
        from bellomberg.storage.weekly_run_store import WeeklyRunBlocked

        def _observe_health(name, result):
            return capture_health(bb, "preflight:" + name, name, result, phase="preflight")

        _probe_acquisitions = {}
        def _probe(name, fn):
            try:
                r = fn()
                _probe_acquisitions[name] = {"status": "RETURNED", "payload_present": r is not None}
                observation = _observe_health(name, r)
                if observation is not None and observation["observation"]["status"] == "UNVERIFIED":
                    tool_health.setdefault("unverified", []).append(name)
                elif observation is not None and observation["observation"]["status"] not in ("OK", "EMPTY"):
                    tool_health["ko"].append(name + " -> " + observation["observation"]["status"])
                elif isinstance(r, dict) and r.get("error"):
                    tool_health["ko"].append(name + " -> " + str(r["error"])[:160])
                elif r is None or (hasattr(r, "__len__") and len(r) == 0):
                    tool_health["ko"].append(name + " -> risposta vuota")
                else:
                    tool_health["ok"].append(name)
                return r
            except WeeklyRunBlocked:
                raise
            except Exception as e:
                _probe_acquisitions[name] = {"status": "FAILED", "exception_type": type(e).__name__}
                _observe_health(name, {"error": "probe_failed"})
                tool_health["ko"].append(name + " -> " + type(e).__name__ + ": " + str(e)[:160])
                return None

        _log("HEALTH-CHECK pre-run dei tool del comitato")
        if portfolio and portfolio.get("n_positions"):
            tool_health["ok"].append("portfolio (" + str(portfolio["n_positions"]) + " posizioni)")
        else:
            tool_health["ko"].append("portfolio -> DB vuoto o illeggibile")
        _macro_health = _observe_health("macro_dashboard", macro)
        if _macro_health is not None and _macro_health["observation"]["status"] not in ("OK", "EMPTY"):
            tool_health["ko"].append("macro_dashboard -> " + _macro_health["observation"]["status"])
        elif macro and macro.get("indicators"):
            tool_health["ok"].append("macro_dashboard (" + str(len(macro["indicators"])) + " indicatori)")
        else:
            tool_health["ko"].append("macro_dashboard -> vuoto/KO")
        _var_contribution = None
        try:
            from bellomberg.portfolio.portfolio_analytics import compute_var_contribution
            _var_contribution = _probe("var_contribution", lambda: compute_var_contribution())
        except WeeklyRunBlocked:
            raise
        except Exception as e:
            _probe_acquisitions["var_contribution"] = {"status": "IMPORT_FAILED", "exception_type": type(e).__name__}
            _observe_health("var_contribution", {"error": "probe_import_failed"})
            tool_health["ko"].append("var_contribution -> import: " + str(e)[:120])
        _quant_advanced_metrics = None
        try:
            from bellomberg.portfolio.advanced_metrics import portfolio_metrics
            _quant_advanced_metrics = _probe("portfolio_metrics", lambda: portfolio_metrics())
        except WeeklyRunBlocked:
            raise
        except Exception as e:
            _observe_health("portfolio_metrics", {"error": "probe_import_failed"})
            tool_health["ko"].append("portfolio_metrics -> import: " + str(e)[:120])
        if _quant_render_enabled:
            _quant_advanced_metrics = _quant_entry(_quant_advanced_metrics, 'preflight.portfolio_metrics')
        try:
            from bellomberg.market_data.news_aggregator import get_feed
            _probe("news_feed", lambda: get_feed(limit=5))
        except WeeklyRunBlocked:
            raise
        except Exception as e:
            _observe_health("news_feed", {"error": "probe_import_failed"})
            tool_health["ko"].append("news_feed -> import: " + str(e)[:120])
        # Audit 11/09 (Fable 5.1, run 10/09 memo #53): Polymarket era irraggiungibile (TLS) per
        # tutta la run e `_tool_health.ko` restava vuoto — il tool rende un dict con count 0 e
        # `fetch_warnings`, che `_probe` legge come risposta sana. Una sonda dedicata: KO con la
        # causa quando ogni endpoint ha fallito, cosi' il Capo lo legge nell'HEALTH-CHECK.
        try:
            from bellomberg.agents.agent_tools import tool_get_polymarket_events
            _pm = tool_get_polymarket_events("fed", max_results=1)
            _pm = _pm if isinstance(_pm, dict) else {}
            _observe_health("polymarket", _pm)
            _fw = [str(w) for w in (_pm.get("fetch_warnings") or [])]
            if _pm.get("error"):
                tool_health["ko"].append("polymarket -> " + str(_pm["error"])[:160])
            elif _fw and not (_pm.get("results") or []):
                tool_health["ko"].append("polymarket -> " + "; ".join(_fw)[:200])
            else:
                tool_health["ok"].append("polymarket (%d risultati)" % len(_pm.get("results") or []))
        except WeeklyRunBlocked:
            raise
        except Exception as e:
            _observe_health("polymarket", {"error": "probe_failed"})
            tool_health["ko"].append("polymarket -> " + type(e).__name__ + ": " + str(e)[:120])
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
        # SONDA DEI MODELLI (audit 11/09, Fable 5.1, run 10/09 memo #53): il modello del red
        # team era respinto da OpenRouter (HTTP 403, gate 18+) e lo si e' scoperto alle 16:15,
        # 45 minuti dopo lo start, a R0 e R1 gia' pagati. Una call da pochi token per ogni slug
        # distinto del .env PRIMA del Round 0: l'esito e' dichiarato (log + `_tool_health` ->
        # prompt del Capo + heartbeat) e la run NON si ferma — i desk hanno il loro modello,
        # il red team resta best-effort. Model string mai cambiate qui.
        try:
            from bellomberg.core.llm_client import sonda_modelli, righe_log_sonda, modello_o_buco as _mob2
            _richieste = [("consigliere", None, 0), ("consigliere", None, 1), ("capo", None, None),
                          ("red_team", None, None), ("reflection", None, None),
                          ("action_extractor", None, None)]
            _richieste += [("consigliere", getattr(_sp, "name", None), _rn)
                           for _sp in SPECIALIST_ORDER for _rn in (0, 1, 2)]
            _slugs = []
            for _f, _a, _r in _richieste:
                try:
                    _s = _mob2(_f, _a, _r)
                except Exception:
                    continue
                if _s and not str(_s).startswith("n.d."):
                    _slugs.append(str(_s))
            _log("SONDA modelli configurati (%d slug distinti)" % len(dict.fromkeys(_slugs)))
            with request_scope(store.request_journal, phase="model_probe", agent="_probe"):
                _esiti = sonda_modelli(_slugs)
            for _l in righe_log_sonda(_esiti):
                _log("  " + _l)
            for _s, _e in _esiti.items():
                if _e.get("usage") is not None or _e.get("request_id") is not None:
                    _probe_usage = dict(_e.get("usage") or {})
                    _probe_usage.update(model=_s, api_calls=1, duration_s=_e.get("durata_s"),
                                        cost_usd=_e.get("cost_usd"),
                                        status="ok" if _e.get("ok") else "api_error")
                    # R-2 F1 (06/10, Opus 5.5): la richiesta della sonda, anche FALLITA, e'
                    # nel registro: la riga di usage la nomina, cosi' il costo incerto resta
                    # attribuito e dichiarato (mai «tentativo senza richieste»)
                    if _e.get("request_id") and _e["request_id"] not in (_probe_usage.get("request_ids") or []):
                        _probe_usage["request_ids"] = list(_probe_usage.get("request_ids") or []) + [_e["request_id"]]
                    _record_side_usage(bb, "_probe:" + _s, _probe_usage)
                if not _e.get("ok"):
                    tool_health["ko"].append("modello " + _s + " -> " + str(_e.get("motivo"))[:160])
        except Exception as e:
            _log("  [!] sonda modelli non eseguita (proseguo): " + type(e).__name__ + ": " + str(e)[:120])
        _request_summary = store.request_journal.summary()
        if _request_summary["unknown_requests"]:
            # Regola PM 05/10/2026: un costo incerto si dichiara, non ferma la run.
            _log("  [!] sonda: %d richieste con costo/esito incerto, DICHIARATE (da riconciliare); la run prosegue"
                 % _request_summary["unknown_requests"])
        bb.data["_tool_health"] = tool_health

        # FRESHNESS CHECK (audit/07 §2-3, P1 + regola PM "mai fallback, precisione"):
        # confronta i dati esterni con lo snapshot della run precedente e marca STALE
        # i valori fermi (funding +10,95% identico per 4 memo) o con osservazione
        # vecchia (il "DXY in risalita" del #42 era un FRED di 11 giorni prima).
        freshness_report = None
        from bellomberg.core.evidence_followup_policy import board_enabled as _followup_enabled, frozen_as_of
        _evidence_followup = _followup_enabled(bb)
        _freshness_as_of = frozen_as_of(store.context['contract']) if _evidence_followup else None
        try:
            from bellomberg.core.freshness import check_and_update
            if _evidence_followup:
                from bellomberg.core.freshness import check_release_freshness, project_macro_observation
            _cur = {}
            for _k, _ind in ((macro or {}).get("indicators") or {}).items():
                if isinstance(_ind, dict) and (_evidence_followup or _ind.get("value") is not None):
                    # prefisso = fonte dichiarata (P1 14/07: le serie native hanno
                    # 'src' ONS/Eurostat/IMF/BCB; quelle FRED restano 'fred:')
                    _src = str(_ind.get("src") or "fred").lower()
                    _cur[_src + ":" + _k] = (project_macro_observation(_ind) if _evidence_followup
                        else {"value": _ind.get("value"), "obs_date": _ind.get("date")})
            try:
                from bellomberg.agents.agent_tools import tool_get_hyperliquid_intel
                _hl = tool_get_hyperliquid_intel()
                # fix 09/10 (Opus 5.5): la data e' l'istante di acquisizione dichiarato dal tool.
                # Un funding fermo al tasso base 10,95% e' NORMALE (premio ~0), non una fonte
                # ferma: la freschezza si misura dalla data, mai dall'identita' del valore.
                _hl_oss = str(_hl.get("fetched_at_utc") or "")[:10] or None
                for _row in (_hl.get("top_10_perps_by_oi") or []):
                    if _row.get("asset") in ("BTC", "ETH", "SOL", "HYPE") \
                            and _row.get("funding_annualized_pct") is not None:
                        _cur["hl_funding:" + _row["asset"]] = {
                            "value": _row["funding_annualized_pct"], "obs_date": _hl_oss,
                            "observation_period": _hl_oss, "retrieved_at": _hl.get("fetched_at_utc")}
            except Exception as _he:
                _log("  [!] freshness: hyperliquid non raggiungibile (" + str(_he)[:80] + ")")
            if _cur or _evidence_followup:
                freshness_report = (check_release_freshness(_cur, _freshness_as_of) if _evidence_followup
                                    else check_and_update(_cur))
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

        # GUARDRAIL BETA nel priming (PM 06/10, Opus 5.5): calcolato UNA volta, prima di R1,
        # lo leggono il punteggio quant (R1) e il blocco del Capo; guasto = {"error": tipo}
        bb.data["_beta_reconcile"] = _calcola_guardrail_beta()
        store.complete("priming", {"macro": macro, "correlation_matrix": correlation_matrix,
            "freshness_report": freshness_report, "tool_health": tool_health,
            "scorecard": _progress_scorecard, "score_error": _progress_score_error,
            "beta_reconcile": bb.data["_beta_reconcile"],
            **({"var_contribution": _var_contribution,
                "var_contribution_acquisition": _probe_acquisitions["var_contribution"]} if _evidence_followup else {}),
            **({"quant_advanced_metrics": _quant_advanced_metrics} if _quant_render_enabled else {})}, bb)
    else:
        # ripresa: il guardrail del priming si RIUSA; checkpoint di una run precedente alla
        # regola 06/10 (chiave assente) = ricalcolato una volta, dichiarato nel log
        _rb_priming = _priming.get("beta_reconcile")
        if _rb_priming is None:
            _log("[!] checkpoint di priming senza guardrail beta (run precedente al 06/10): ricalcolato una volta")
            _rb_priming = _calcola_guardrail_beta()
        bb.data["_beta_reconcile"] = _rb_priming
        macro = _priming["macro"]
        correlation_matrix = _priming["correlation_matrix"]
        freshness_report = _priming["freshness_report"]
        tool_health = _priming["tool_health"]
        _progress_scorecard = _priming["scorecard"]
        _progress_score_error = _priming["score_error"]

    # ROUND 0 (recon) + ROUND 1 (draft)
    from bellomberg.agents import weekly_lifecycle as _wl
    for r in [0, 1]:
        store.update(phase="round_" + str(r))
        run_round(bb, r)
        # Comitato a lacune (PM 04/10): quorum perso = stop PRIMA di pagare altri round.
        try:
            _wl.quorum_still_reachable(bb, store)
        except Exception:
            _wl.committee_summary(bb, store)
            raise

    # AUTO299: a desk omitting the tool must not silently omit the DB holdings.
    # Existing attempts (including failures) are never automatically retried.
    if is_research_mode(bb):
        store.update(phase='research_dossier')
        try:
            _present, _missing = _wl.committee_quorum(bb, store)
        except Exception:
            _wl.committee_summary(bb, store)
            raise
        sealed = seal_research_thesis(bb, desks=_present, missing=_missing)
        if store.get('research_dossier') is None:
            store.complete('research_dossier', sealed, bb)
        elif store.get('research_dossier') != sealed:
            raise RuntimeError('Research dossier checkpoint differs from the accepted committee seal')
    elif store.get("valuation_coverage") is None:
        _ensure_portfolio_valuations(bb, portfolio)
        store.complete("valuation_coverage", {"complete": True}, bb)

    # RED TEAM (#181): TRA R1 e R2 (voce P1 "tardivo": prima girava DOPO l'R2 e
    # gli specialisti non potevano mai replicare). Attacca i draft R1; la critica
    # entra nella blackboard di R2 (whitelist) + nel DB via Blackboard.write.
    if store.get("red_team") is None:
        store.update(phase="red_team")
        try:
            from bellomberg.agents.red_team import run_red_team
            _log("=" * 60)
            _log("RED TEAM (devil's advocate) challenge sui draft R1")
            with request_scope(store.request_journal, phase="red_team", agent="_red_team", round_n=1):
                crit = run_red_team(bb, portfolio_data=portfolio, memory_db=db)
            if crit:
                _log("Red team critica: " + str(len(crit)) + " chars (visibile in R2)")
        except Exception as e:
            _log("[!] Red team: " + str(e))
            bb.record_run_failure(e, desk="_red_team", round_n=1)
            if bb.data.get("_red_team_gap") is None:
                raise
            crit = None   # lacuna dichiarata (PM 04/10): la run prosegue senza contraddittorio

        bb.raise_if_run_blocked()
        from bellomberg.agents.red_team import motivo_critica_non_utilizzabile
        _red_report = bb.data.get("_red_team", {}).get(1) or crit
        _red_reason = motivo_critica_non_utilizzabile(_red_report)
        if bb.data.get("_red_team_gap") is None and (_red_reason or "CRITICA TRONCATA" in str(_red_report)):
            bb.record_run_failure(RuntimeError(_wl.RED_TEAM_UNUSABLE + (_red_reason or "CRITICA TRONCATA")),
                                  desk="_red_team", round_n=1)
            if bb.data.get("_red_team_gap") is None:
                raise RuntimeError(_red_reason or str(_red_report))
        red_payload = {'report': _red_report}
        if bb.data.get("_red_team_gap") is not None:
            red_payload['gap'] = bb.data["_red_team_gap"].get("message")
            _log("[!] Red Team in LACUNA DICHIARATA: R2 e Capo procedono senza contraddittorio")
        if is_research_mode(bb):
            red_payload['research_ref'] = research_reference(bb)
        store.complete("red_team", red_payload, bb)
    if is_research_mode(bb) and store.get('red_team').get('research_ref') != research_reference(bb):
        raise RuntimeError('Red Team does not refer to the sealed research dossier')

    # ROUND 2 (cross-review + replica al red team)
    store.update(phase="round_2")
    run_round(bb, 2)
    if is_research_mode(bb):
        from bellomberg.agents.weekly_lifecycle import validate_research_reviews
        validate_research_reviews(bb, _wl.reviewed_desks(bb, [cls.name for cls in _classes_for_round(2)]))

    _synthesis = store.get("synthesis_context")
    if _synthesis is None:
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
        # 05/10 (Opus 5.5): beta vietato per le coperture con OGNI verdetto != RECONCILED; fuori
        # da RECONCILED al Capo arrivano solo verdetto, motivo e divieto (niente valori per fonte
        # ne' intervallo/mediana). Guardrail rotto o senza verdetto = blocco che lo DICHIARA e
        # vieta il beta (review R-4: prima la riga spariva e con lei il divieto).
        # 06/10: riusa il guardrail del priming (stesso verdetto visto dal quant in R1).
        try:
            from bellomberg.portfolio.advanced_metrics import testo_guardrail_beta_capo
            _rb = bb.data.get("_beta_reconcile")
            if _rb is None:   # non dovrebbe: il priming lo scrive sempre
                _log("[!] guardrail beta assente dalla blackboard: calcolato ora, una volta")
                _rb = bb.data["_beta_reconcile"] = _calcola_guardrail_beta()
            if not (isinstance(_rb, dict) and _rb.get("verdict")):
                _log("[!] guardrail beta senza verdetto: beta vietato al Capo")
            _line = testo_guardrail_beta_capo(_rb)
            # review R-SEG 06/10 (S2): ripresa in un altro giorno = il Capo sa che e' vecchio
            _quando = str(_rb.get("calcolato_il") or "")[:10] if isinstance(_rb, dict) else ""
            if _quando and _quando != datetime.now().date().isoformat():
                _line += ("\nNB: guardrail calcolato il " + _quando + " (priming di questa run) e "
                          "riusato nella ripresa di oggi: non ricalcolato.")
        except Exception as e:
            _log("[!] guardrail beta non disponibile: " + type(e).__name__)
            _line = ("\n\n=== GUARDRAIL BETA (riconciliazione 3 motori) ===\n"
                     "GUARDRAIL BETA non disponibile (" + type(e).__name__ + "): il beta NON e' un "
                     "argomento decisionale valido (vietati verdetti di hedge basati sul beta) "
                     "finche' non riconciliato.")
        sizing_context = (sizing_context or "") + _line

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

        from bellomberg.core.source_health import context_block as _source_health_context
        _source_health_block = _source_health_context(bb, store.context.get("language", "it"))
        if _source_health_block:
            sizing_context = (sizing_context or "") + "\n\n" + _source_health_block

        # HEALTH-CHECK -> prompt del Capo: i tool KO vanno DICHIARATI nel memo
        if tool_health["ko"]:
            _hline = ("\n\n=== HEALTH-CHECK PRE-RUN: TOOL NON DISPONIBILI ===\n- "
                      + "\n- ".join(tool_health["ko"])
                      + "\nREGOLA: questi dati NON hanno alimentato la run. Il memo DEVE "
                        "dichiarare esplicitamente il pezzo mancante; VIETATE affermazioni "
                        "che presuppongono quei tool (es. decomposizione VaR se "
                        "var_contribution e' KO).")
            if _source_health_block:
                _hline = _hline.replace("questi dati NON hanno alimentato la run",
                    "questi controlli pre-run non attestavano disponibilita; gli esiti successivi sono separati in SOURCE HEALTH")
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

        store.complete("synthesis_context", {"risk_data": risk_data, "sizing_context": sizing_context,
                                             "scoring_context": scoring_context}, bb)
    else:
        risk_data = _synthesis["risk_data"]
        sizing_context = _synthesis["sizing_context"]
        scoring_context = _synthesis["scoring_context"]

    if research_gate_enabled(store.context['contract']):
        _research_gate_checks = _research_publication_checks(bb, store)
        sizing_context = (sizing_context or '') + '\n\nRESEARCH PUBLICATION CHECKS:\n' + json.dumps(
            _research_gate_checks, ensure_ascii=False) + (
            '\nDCF is not applicable. The final gate binds each exact BUY/ADD row to this run, '
            'its sealed issuer dossier, committee reports and eligible source receipts. This verifies '
            'provenance and source availability, not semantic truth or economic approval. '
            'Existing instrument-nature exceptions remain explicitly labelled SENZA VALUTAZIONE. '
            'Judgments and assumptions belong to the committee; approval belongs to the PM. '
            'State actual source gaps in the BLUF; do not present a withdrawn proposal as executable.')

    _capo_saved = store.get("capo")
    if _capo_saved is None:
        store.update(phase="capo")
        # CAPO synthesis (con memoria persistente)
        _log("=" * 60)
        _log("CAPO synthesis (memory-aware)")
        _log("=" * 60)
        bb.mark_specialist_start("capo", 3)
        _capo_t0 = time.perf_counter()
        if not is_research_mode(bb):
            sizing_context = (sizing_context or "") + "\n\n" + preparation_status_text(
                bb.data["_valuation_preparation"], language=bb.language)
        from bellomberg.core.llm_client import request_scope
        _comitato_pre_capo = _wl.committee_summary(bb, store)
        if not _comitato_pre_capo.get("high_conviction_allowed", False):   # ogni modalita' (V0-REDTEAM 05/10)
            sizing_context = (sizing_context or "") + ((
                "\n\nSTATO DEL COMITATO NON CALCOLATO in questa run: la presenza del Red Team NON e' "
                "verificata (R-0RT F-E); per prudenza nessuna proposta puo' avere confidence ALTA; il "
                "codice declassa ALTA a MEDIA nella colonna confidence della ACTION TABLE e lo dichiara nel memo.")
                if _comitato_pre_capo.get("status") == "unavailable" else (
                "\n\nRED TEAM MANCANTE O PARZIALE in questa run (regola PM 04/10): nessuna proposta "
                "puo' avere confidence ALTA; il codice declassa ALTA a MEDIA nella colonna confidence "
                "della ACTION TABLE e lo dichiara nel memo."))
        with request_scope(store.request_journal, phase="capo", agent="capo", round_n=3):
            memo, capo_usage = run_capo(bb, portfolio_data=portfolio, memory_db=db, sizing_context=sizing_context, scoring_context=scoring_context)
        _capo_dur = time.perf_counter() - _capo_t0
        # Il Capo entra nel conto costi come gli specialisti. cache_ttl=None: non usa
        # prompt caching (capo.py, messages.create senza cache_control).
        # Se run_capo ha restituito il dict d'errore (0/0 token), la riga nasce api_error:
        # un Capo morto si dichiara, non sparisce dai costi.
        _record_capo_usage(bb, capo_usage, _capo_dur)
        bb.mark_specialist_done("capo")

        from bellomberg.agents.weekly_lifecycle import validate_memo
        from bellomberg.storage.weekly_run_store import WeeklyRunBlocked as _CapoNonCompleto
        try:
            validate_memo(memo, capo_usage)
        except _CapoNonCompleto as _capo_error:
            # PM 04/10: memo PARZIALE marcato INCOMPLETO, nessuna decisione, nessuna email.
            _log("[!] " + str(_capo_error) + ": memo parziale INCOMPLETO, nessuna decisione, nessuna email")
            _chiudi_prerun_filing()
            return _wl.capo_partial_package(store, bb, sys.modules[__name__], memo=memo,
                                            usage=capo_usage, error=_capo_error)
        capo_payload = {'memo': memo, 'usage': capo_usage}
        if is_research_mode(bb):
            capo_payload['research_ref'] = research_reference(bb)
        store.complete("capo", capo_payload, bb)
        _prev_partial = store.status().get("capo_partial")
        if _prev_partial:
            # ripresa riuscita: il memo parziale di prima resta solo come storico
            store.update(capo_partial=None, capo_partial_superseded=_prev_partial)
    else:
        memo, capo_usage = _capo_saved["memo"], _capo_saved["usage"]

    # Comitato a lacune (PM 04/10): stato del comitato (desk, Red Team, Capo) e, con il Red
    # Team mancante, ALTA -> MEDIA applicato dal codice PRIMA del gate (decisioni coerenti).
    # Deterministico: in ripresa si ricalcola dallo stesso checkpoint 'capo'.
    _committee = _wl.committee_summary(bb, store, memo=memo, usage=capo_usage)
    _conviction_block = ""
    if not _committee.get("high_conviction_allowed", False):   # ogni modalita' (V0-REDTEAM 05/10)
        memo, _capped, _residual = _wl.cap_high_conviction(memo)
        _conviction_block = _wl.conviction_cap_block(_capped, _residual, store.context.get("language"),
                                                     comitato_non_calcolato=_committee.get("status") == "unavailable")

    # PUBLICATION GATE (Andrea, ported): il testo intatto del Capo resta in un
    # sidecar *_capo_raw.md mai sovrascritto (stessa via congelata degli artefatti
    # della run, idempotente in ripresa); il memo pubblicato e' una proiezione.
    capo_source_memo = str(memo or "")
    _raw_sidecar_path = None
    _raw_sidecar_error = None
    try:
        from bellomberg.agents.weekly_lifecycle import write_frozen_text
        os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)
        _raw_sidecar_path = os.path.join(RESEARCH_NOTES_DIR,
                                         "bellomberg_memo_" + str(memo_id) + "_capo_raw.md")
        # Exclusive/frozen create: a run never overwrites a prior Capo source artifact.
        write_frozen_text(_raw_sidecar_path, capo_source_memo)
        _log("Capo source preserved: " + _raw_sidecar_path)
    except Exception as _raw_error:
        _raw_sidecar_error = "testo sorgente Capo non archiviato: " + type(_raw_error).__name__ + ": " + str(_raw_error)[:160]
        _log("[!] " + _raw_sidecar_error)

    _validated = store.get("memo_validated")
    if _validated is None:
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

        # Controllo fatti flag-only: checkpoint prima della proiezione validata.
        # Il validator e i binding continuano a ricevere il source canonico.
        from bellomberg.core.memo_facts_context import checkpoint_memo_facts
        _facts_block = checkpoint_memo_facts(store, capo_source_memo)
        if _facts_block:
            memo = memo + "\n\n" + _facts_block

        from bellomberg.core.source_health import checkpoint_source_health
        _source_health_block = checkpoint_source_health(store, capo_source_memo)
        if _source_health_block:
            memo = memo + "\n\n" + _source_health_block

        # ACTION VALIDATOR #191 (v1 SOLO FLAG, scelta PM 13/07): appende avvertimenti al
        # memo (sizing sforato, riproposte mai eseguite); i numeri del Capo restano
        # intoccati e un guasto del validator non tocca la run.
        try:
            from bellomberg.agents.action_validator import build_validator_block
            _vblock = build_validator_block(capo_source_memo, bb.data.get("_sizing"), db, exclude_memo_id=memo_id,
                                            mandato=_mandato_run, publication_contract=store.context['contract'],
                                            research_checks=_research_publication_checks(bb, store, capo_source_memo)
                                            if research_gate_enabled(store.context['contract']) else None)
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
            _xblock, _sanity_pairs = detect_sanity_exclusions(memo, publication_contract=store.context['contract'])
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

        # COMITATO: LACUNE DICHIARATE (PM 04/10, elenco in coda, scelta «C»): il blocco lo
        # scrive il codice dal registro della run, non il Capo.
        for _cblock in (_wl.committee_memo_block(_committee), _conviction_block):
            if _cblock:
                memo = memo + "\n\n" + _cblock
        if _committee.get("status") != "complete":
            _log("COMITATO: " + str(_committee.get("status")) + " (blocco lacune nel memo)")

        # Publication gate (_publication_gate): decisions, assessments and hard sanity
        # closures are committed before either PDF renderer sees the memo. The memo
        # validated here IS the publication projection, so every later artifact
        # (Markdown, PDF, DB row, recovery) carries the same fail-closed text.
        _analysis_memo = memo
        # Difesa in piu' (PM 04/10): sotto quorum o Capo non completo NESSUNA decisione si
        # persiste. Riepilogo non calcolato = non ammesso (mai «ammesso» per default).
        # Decisione main 06/10: decisioni non ammesse NON fermano la run. Memo e PDF si pubblicano
        # fail-closed (gate senza registro: nessuna decisione persistita, righe d'ingresso non
        # operative col motivo), le decisioni restano NON ammesse e dichiarate, run «incompleta».
        _decisioni_non_ammesse = None
        if not _committee.get("decisions_allowed", False):
            _decisioni_non_ammesse = ("decisioni non ammesse dal comitato (stato "
                                      + str(_committee.get("status")) + "): nessuna decisione registrata")
            _log("[!] " + _decisioni_non_ammesse + "; memo e PDF pubblicati, run incompleta")
        _gate = _publication_gate(bb, None if _decisioni_non_ammesse else db, store, memo_id, memo,
                                  capo_source_memo, _decisioni_non_ammesse or _raw_sidecar_error, _sanity_pairs)
        publication = _gate["publication"]
        decision_ids = _gate["decision_ids"]
        _decisions_error = _gate["decisions_error"]
        _hard_pairs = _gate["hard_pairs"]
        memo = publication["memo_markdown"]
        store.complete("memo_validated", {"memo": memo, "capo_usage": capo_usage,
                                           "sanity_pairs": _sanity_pairs,
                                           "analysis_memo": _analysis_memo,
                                           "publication": {k: v for k, v in publication.items()
                                                           if k != "source_markdown"},
                                           "hard_pairs": [list(pair) for pair in _hard_pairs],
                                           "decision_ids": decision_ids,
                                           "decisions_error": _decisions_error,
                                           **({"decisions_not_allowed": _decisioni_non_ammesse}
                                              if _decisioni_non_ammesse else {})}, bb)
    else:
        capo_usage = _validated["capo_usage"]
        _decisioni_non_ammesse = _validated.get("decisions_not_allowed")   # ripresa: resta non ammesso
        _analysis_memo = _validated.get("analysis_memo", _validated["memo"])
        _restored = _restore_validated(
            _validated, capo_source_memo,
            lambda _memo, _pairs: _publication_gate(bb, db, store, memo_id, _memo, capo_source_memo,
                                                    _raw_sidecar_error, _pairs))
        memo = _restored["memo"]
        _sanity_pairs = _restored["sanity_pairs"]
        publication = _restored["publication"]
        _hard_pairs = _restored["hard_pairs"]
        decision_ids = _restored["decision_ids"]
        _decisions_error = _restored["decisions_error"]
    publication_memo = memo
    store.update(analytical_status="complete", phase="artifacts")
    # Save memo markdown archivio
    os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)
    md_path = os.path.join(RESEARCH_NOTES_DIR,
                            "bellomberg_memo_" + str(memo_id) + ".md")
    from bellomberg.agents.weekly_lifecycle import write_frozen_text
    write_frozen_text(md_path, memo)
    _log("Markdown archive: " + md_path)

    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    from bellomberg.core.reflection_policy import (enabled as _reflection_enabled, prepare_input as _reflection_input,
        result_for as _reflection_result, verified_lesson as _verified_lesson)
    def _reflection_request(groups):
        from bellomberg.agents.reflection import policy_request
        return policy_request(groups)
    _reflection36 = _reflection_enabled(store.context.get('contract', {}))
    _reflection = store.get("reflection")
    _reflection_evidence = (_reflection_input(store, request_factory=_reflection_request) if _reflection36 else None)
    if _reflection36 and _reflection is not None:
        _verified_lesson(store)
    if _reflection is None:
        # REFLECTION #210 (post-run, Sonnet): lezione sintetica ancorata agli esiti
        # dello scorekeeper, salvata per il priming della PROSSIMA run. Best-effort.
        _lesson = None
        _progress_reflection_status = "unavailable"
        _refl_result = {}
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
            _refl_result = {}
            try:
                with request_scope(store.request_journal, phase="reflection", agent="_reflection"):
                    _lesson = generate_lesson(publication_memo, memo_id=memo_id, usage_out=_refl_usage,
                                              eligible_memo_ids={m["id"] for m in db.get_completed_weekly_memos(n=None)},
                                              **({'policy_input': _reflection_evidence, 'result_out': _refl_result}
                                                 if _reflection36 else {}))
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
            if _reflection36 and isinstance(e, WeeklyRunBlocked):
                raise
            _log("[!] reflection skipped: " + str(e))

        if _reflection36:
            _reflection_payload = (_refl_result if _refl_result else
                _reflection_result(_reflection_evidence, cause='GENERATION_UNAVAILABLE'))
            store.complete("reflection", _reflection_payload, bb)
        else:
            store.complete("reflection", {"lesson": _lesson, "status": _progress_reflection_status}, bb)
    else:
        _lesson, _progress_reflection_status = _reflection["lesson"], _reflection["status"]

    # Blackboard JSON archive retains diagnostics; it is separate from the memo.
    debug_path = md_path.replace(".md", "_blackboard.json") if md_path else None
    if debug_path:
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump({"data": bb.data, "tool_log": bb.tool_log, "language": bb.language,
                          "valuation_results": bb.valuation_results,
                          "valuation_attempts": bb.valuation_attempts},
                          f, indent=2, default=str)
        except Exception:
            pass

    # PDF MEMO PRINCIPALE (#183 istituzionale Aurum-style, fallback al builder classico)
    from bellomberg.agents.weekly_lifecycle import (
        restore_delivery_bundle, preserve_delivery_bundle, deliver_once, render_pdf_once, WeeklyRunBlocked)
    _artifact_bundle = restore_delivery_bundle(store, sys.modules[__name__])
    _saved_artifacts = (_artifact_bundle or {}).get("artifacts", [])
    pdf_memo_path = next((row["path"] for row in _saved_artifacts
                         if row["kind"] == "PDF" and row.get("role") == "memo"), None)
    _render_saved = store.get("render_context")
    nav_history = _render_saved.get("nav_history") if _render_saved is not None else None
    if _render_saved is not None:
        risk_data = _render_saved.get("risk_data")
    if _render_saved is None and risk_data is None and not _quant_render_enabled:
        try:
            from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
            risk_data = compute_portfolio_risk()
            if isinstance(risk_data, dict) and risk_data.get("error"):
                _log("  [!] risk metrics: " + str(risk_data.get("error")))
                risk_data = None
        except Exception as e:
            _log("[!] risk metrics for PDF skipped: " + str(e))
    if _render_saved is None:
        if _quant_render_enabled:
            _nav_entry = _quant_capture(store, 'nav_history')
            nav_history = _nav_entry['payload']
        else:
            try:
                from bellomberg.portfolio.portfolio_analytics import compute_nav_history
                nav_history = compute_nav_history()
                if isinstance(nav_history, dict) and nav_history.get("error"):
                    nav_history = None
            except Exception as e:
                _log("[!] nav history for PDF skipped: " + str(e))
        store.complete("render_context", {"risk_data": risk_data, "nav_history": nav_history}, bb)
    _quant_snapshot = _quant_prepare(store) if _quant_render_enabled and _artifact_bundle is None else None
    if pdf_memo_path is None:
        try:
            from bellomberg.reporting.pdf_institutional import build_institutional_memo
            pdf_memo_path = render_pdf_once(store, sys.modules[__name__], build_institutional_memo,
                memo_markdown=publication_memo, portfolio_data=portfolio,
                risk_data=risk_data, nav_history=nav_history,
                sizing_data=bb.data.get("_sizing"),
                scoring_data=bb.data.get("_score_cache"),
                title_date=store.context["research_started_at"].split("T")[0])
            if pdf_memo_path:
                _log("PDF MEMO (istituzionale): " + pdf_memo_path)
        except WeeklyRunBlocked:
            raise
        except Exception as e:
            store.fail(e, phase="render_pdf")
            _log("[!] PDF istituzionale error: " + str(e))
    if not pdf_memo_path:  # fallback al builder classico (mai senza PDF)
        try:
            from bellomberg.reporting.pdf_report import build_pdf_report
            pdf_memo_path = render_pdf_once(store, sys.modules[__name__], build_pdf_report,
                memo_markdown=publication_memo, portfolio_data=portfolio,
                macro_data=macro, options_data_dict={})
            if pdf_memo_path:
                _log("PDF MEMO (classico fallback): " + pdf_memo_path)
        except WeeklyRunBlocked:
            raise
        except Exception as e:
            store.fail(e, phase="render_pdf_fallback")
            _log("[!] PDF memo fallback error: " + str(e))

    # PDF APPENDICE QUANT
    pdf_appendix_path = next((row["path"] for row in _saved_artifacts
                             if row["kind"] == "PDF" and row.get("role") == "appendix"), None)
    if _artifact_bundle is None:
        try:
            try:
                from bellomberg.reporting.charts_quant import build_quant_appendix_v2 as build_quant_appendix  # #178
            except ImportError:
                from bellomberg.reporting.charts_agent import build_quant_appendix
            pdf_appendix_path = render_pdf_once(store, sys.modules[__name__], build_quant_appendix, role="appendix",
                blackboard=bb, portfolio_data=portfolio,
                macro_data=macro, options_data_dict={},
                correlation_data=correlation_matrix,
                **({"quant_snapshot": _quant_snapshot} if _quant_render_enabled else {}))
            if pdf_appendix_path:
                _log("PDF APPENDICE: " + pdf_appendix_path)
        except WeeklyRunBlocked:
            raise
        except Exception as e:
            _log("[!] PDF appendix error: " + str(e))

    # DCF EXCEL FILES
    from bellomberg.reporting.valuation_delivery import save_manifest, record_email_outcome
    from bellomberg.agents.weekly_lifecycle import build_weekly_delivery, weekly_email_body
    delivery = build_weekly_delivery(bb, roots=[MODELS_DIR, REPORT_DIR])
    delivery["memo_id"] = memo_id
    from hashlib import sha256
    delivery["memo_sha256"] = sha256(memo.encode("utf-8")).hexdigest()
    delivery_path = md_path.replace(".md", "_valuations.json")
    save_manifest(delivery_path, delivery)
    from bellomberg.agents.weekly_lifecycle import validate_delivery_manifest
    validate_delivery_manifest(delivery)
    dcf_files = delivery["attachments"]
    if dcf_files:
        _log("DCF Excel files: " + str(len(dcf_files)))

    from bellomberg.agents.weekly_lifecycle import file_receipt
    file_receipt(pdf_memo_path, kind="PDF")
    preserve_delivery_bundle(store, bb, md_path=md_path, pdf_path=pdf_memo_path,
        appendix_path=pdf_appendix_path, delivery=delivery,
        allowed_roots=[MODELS_DIR, REPORT_DIR, RESEARCH_NOTES_DIR])
    _persistence_ok = False
    # UPDATE memo in DB con paths finali + chunks ChromaDB
    if db and memo_id:
        try:
            # Update memo row con paths e full markdown
            with db._conn() as conn:
                conn.execute("""UPDATE memos SET full_markdown=?, pdf_path=?, appendix_path=?,
                                dcf_files=?, capo_tokens_in=?, capo_tokens_out=? WHERE id=?""",
                              (publication_memo, pdf_memo_path, pdf_appendix_path,
                               json.dumps(dcf_files), capo_usage["input_tokens"],
                               capo_usage["output_tokens"], memo_id))
            # Re-embed in ChromaDB
            if db.col_memos:
                chunks = db._chunk_markdown(publication_memo)
                if chunks:
                    # audit/11 §2: con gli stessi id chromadb add() MANTIENE il documento
                    # vecchio -> il chunk 0 restava '[IN PROGRESS]' per sempre. upsert.
                    db.col_memos.upsert(
                        documents=chunks,
                        metadatas=[{"memo_id": memo_id, "chunk_idx": i} for i in range(len(chunks))],
                        ids=["memo_" + str(memo_id) + "_chunk_" + str(i) for i in range(len(chunks))]
                    )
            # Decisions, assessments and hard sanity closures were persisted by the
            # publication gate BEFORE rendering (from the untouched Capo source).
            # Here they become final for the weekly lifecycle; a gate that could not
            # persist them is retried once with the snapshot that was published.
            _finalize_error = None
            if _decisioni_non_ammesse:
                # Decisione main 06/10: nessun nuovo tentativo di registrare decisioni non ammesse.
                decision_ids, _finalize_error, _decisions_error = [], _decisioni_non_ammesse, None
            if _decisions_error is not None:
                # REV R1/R2: retry idempotente e mai bloccante per sempre. Il memo e' gia'
                # congelato e dichiara cio' che il gate non ha confermato; il registro si
                # riallinea a lui e un guasto residuo resta DICHIARATO nel payload e nel log.
                _log("[!] Decisioni non finalizzate dal gate: " + str(_decisions_error) + " - nuovo tentativo")
                decision_ids, _finalize_error = _finalize_publication_decisions(
                    bb, db, store, memo_id, capo_source_memo, publication, decision_ids)
                if _finalize_error:
                    _log("[!] FINALIZZAZIONE CON GUASTO DICHIARATO (la consegna procede): " + _finalize_error)
                _decisions_error = None
            _log("Decisions extracted from ACTION TABLE: " + str(len(decision_ids)))
            store.complete("decisions_finalized", {"ids": decision_ids, "error": _finalize_error,
                                                   **({"not_allowed": _decisioni_non_ammesse}
                                                      if _decisioni_non_ammesse else {})}, bb)
            _persistence_ok = True
        except Exception as e:
            _log("[!] DB memo finalize failed: " + str(e))
            raise
        # Consumo LLM riga per riga (agente+round). Try separato: le colonne
        # memos.capo_tokens_in/out restano (le leggono altri), questo e' il dettaglio.
        try:
            from bellomberg.agents.weekly_lifecycle import persist_usage_once
            persist_usage_once(store, bb)
            _log("LLM usage rows saved: " + str(len(bb.usage_log)))
        except Exception as e:
            _log("[!] save_llm_usage failed: " + str(e))
            raise

    # EMAIL
    all_attachments = []
    if pdf_memo_path: all_attachments.append(pdf_memo_path)
    if pdf_appendix_path: all_attachments.append(pdf_appendix_path)
    all_attachments.extend(dcf_files)
    # Audit 11/09 (Fable 5.1): il corpo dell'email dichiara OGNI valutazione chiesta dai desk
    # (FV o n.d. col motivo) e se manca l'Excel lo dice — prima il piede prometteva "DCF
    # Excel models" anche con zero .xlsx e il FV n.d. restava solo dentro il PDF.
    try:
        from bellomberg.reporting.email_sender import corpo_azioni
        _corpo_email = weekly_email_body(bb, delivery)
        _corpo_email += "\n" + corpo_azioni(publication.get("assessments") or [])
    except Exception as _ce:
        _corpo_email = ("<p>[esito valutazioni non costruito: %s: %s]</p>"
                        % (type(_ce).__name__, str(_ce)[:120]))
        _log("[!] corpo email valutazioni non costruito: " + type(_ce).__name__ + ": " + str(_ce)[:120])
    if pdf_memo_path and send_email and not _committee.get("automatic_email_allowed", False):
        # Difesa in piu' (PM 04/10): comitato senza email automatica = nessun invio, dichiarato.
        sent = False
        store.update(delivery_requested=True,   # chiesta e non partita: run incompleta, dichiarata
                     email_blocked_reason="Email automatica non ammessa dal comitato (stato "
                     + str(_committee.get("status")) + ")")
        _log("[!] Email settimanale NON inviata: email automatica non ammessa dal comitato")
    elif pdf_memo_path:
        sent = deliver_once(store, sys.modules[__name__], all_attachments,
            body_extra=_corpo_email, delivery=delivery, send_email=send_email)
    else:
        sent = False
        _log("[!] Email settimanale omessa: memo PDF non disponibile, nessuno snapshot pubblicabile")
    record_email_outcome(delivery, sent)
    save_manifest(delivery_path, delivery)

    from bellomberg.agents.weekly_lifecycle import finish_status
    outcome = finish_status(store, bb, pdf_path=pdf_memo_path, md_path=md_path,
        appendix_path=pdf_appendix_path, delivery=delivery, delivery_path=delivery_path,
        persistence_ok=_persistence_ok, sent=sent)
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
    _log(BRAND_NAME + " v" + VERSION + " - " + outcome["status"].upper() + " in " + str(int(elapsed)) + "s")
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
    # Fase D: il contesto filing e' gia' stato scritto; i titoli non ancora partiti non servono piu'.
    _chiudi_prerun_filing()
    return outcome


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run or explicitly recover Consigliere")
    parser.add_argument("--resume-memo-id", type=int)
    parser.add_argument("--delivery-only", action="store_true")
    parser.add_argument("--authorize-new-ai", action="store_true")
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--acknowledge-uncertain-email", action="store_true",
                        help="Conferma del PM: ripete un invio precedente INCERTO (possibile duplicato)")
    parser.add_argument("--outcome")
    parser.add_argument("--task-id")
    args = parser.parse_args()

    def _write_cli_outcome(outcome):
        if args.outcome:
            from bellomberg.agents.specialists.base import scrivi_file_atomico
            ok, error = scrivi_file_atomico(args.outcome, json.dumps(
                {**outcome, "task_id": args.task_id}, ensure_ascii=False, default=str))
            if not ok:
                raise RuntimeError("Esito finale non persistito: " + str(error))
    try:
        outcome = run_multi_agent(resume_memo_id=args.resume_memo_id, delivery_only=args.delivery_only,
                                   authorize_new_ai=args.authorize_new_ai, send_email=not args.no_email,
                                   acknowledge_uncertain_email=args.acknowledge_uncertain_email)
        _write_cli_outcome(outcome)
        if outcome.get("status") != "completed":
            raise SystemExit(2)
    except BaseException as e:
        _chiudi_prerun_filing()  # run interrotta: nessun aggiornamento filing ancora in coda
        _write_cli_outcome(_LAST_WEEKLY_OUTCOME or {"status": "failed", "error": str(e),
            "analytical_status": "incomplete", "artifact_status": "unknown", "delivery_status": "not_attempted"})
        if not _LAST_WEEKLY_OUTCOME:
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
                    "finished_at": datetime.now().isoformat(timespec="seconds"), "status": "failed"}))
                if not _ok:
                    # l'esito non si ignora (review 27/08): un heartbeat di crash non
                    # scritto lascia F4 su «running: true» — almeno lo dice il log
                    print("[!] heartbeat di crash NON scritto (F4 restera' su running): "
                          + str(_err), flush=True)
            except Exception:
                pass
        raise  # rc != 0 preservato per il watchdog della API
