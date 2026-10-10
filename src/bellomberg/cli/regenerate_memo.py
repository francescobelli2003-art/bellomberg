"""
regenerate_memo.py — Ripresa esplicita dello stesso memo tramite il percorso
ordinario Consigliere e i suoi checkpoint nativi. Conserva richieste, report,
decisioni e contesto originali. I memo precedenti privi di checkpoint restano
conservati ma non sono convertiti implicitamente in una nuova run.

USO: python regenerate_memo.py --memo-id ID --delivery-only [--send-email]
      python regenerate_memo.py --memo-id ID --authorize-new-ai
La selezione e' esplicita. La consegna non richiama AI; la continuazione conserva
il contesto originale e richiede autorizzazione per le fasi mancanti.
"""
import os, sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from bellomberg.storage.memory_db import MemoryDB
from bellomberg.agents.specialists.base import Blackboard
from bellomberg.agents.capo import run_capo
from bellomberg.core.paths import MODELS_DIR, REPORT_DIR, RESEARCH_NOTES_DIR
from bellomberg.core.language import capture_language, language_context, scoped_language, text, validate_language


def _log(m): print("[" + datetime.now().strftime("%H:%M:%S") + "] " + m)


def _recovery_language(memo_language, report_languages, explicit=None):
    if explicit is not None:
        selected = validate_language(explicit)
        if memo_language in ("it", "en") and selected != memo_language:
            raise ValueError("traduzione storica richiede nuova versione / historical translation requires a new version")
        return selected
    languages = [memo_language, *report_languages]
    if not languages or any(value not in ("it", "en") for value in languages) or len(set(languages)) != 1:
        raise ValueError("Lingua storica ignota o discordante: specifica --language it|en / "
                         "Unknown or conflicting historical language: specify --language it|en")
    return languages[0]


def main(language=None, *, memo_id=None, delivery_only=False, authorize_new_ai=False, send_email=False):
    if memo_id is not None:
        from bellomberg.agents.consigliere_multi import run_multi_agent
        if language is not None:
            db = MemoryDB()
            with db._conn() as conn:
                row = conn.execute("SELECT output_language FROM memos WHERE id=?", (memo_id,)).fetchone()
            if row is None:
                raise ValueError("Memo selezionato inesistente / Selected memo does not exist")
            _recovery_language(row[0], [row[0]], language)
        return run_multi_agent(resume_memo_id=memo_id, delivery_only=delivery_only,
                               authorize_new_ai=authorize_new_ai, send_email=send_email)
    db = MemoryDB()
    with db._conn() as conn:
        memo = conn.execute("SELECT id,output_language,full_markdown FROM memos "
                            "WHERE substr(COALESCE(notes,''),1,11) <> 'trade_idea:' ORDER BY id DESC LIMIT 1").fetchone()
        if not memo:
            print("Nessun memo nel DB. / No memo in the database."); return
        memo_id = memo["id"]
        rows = conn.execute(
            "SELECT specialist, round_n, content, output_language FROM specialist_reports "
            "WHERE memo_id=? ORDER BY round_n", (memo_id,)).fetchall()
    if not rows:
        print(f"Nessun report salvato per memo #{memo_id}: niente da recuperare "
              f"(la run si e' bloccata prima di finalizzare i Round 2). / "
              f"No saved reports for memo #{memo_id}: nothing to recover "
              f"(the run stopped before finalizing Round 2)."); return

    raise ValueError("Seleziona --memo-id " + str(memo_id) + " e --delivery-only oppure --authorize-new-ai; "
                     "nessun recupero a pagamento implicito dell'ultimo memo / Select a memo and recovery mode")



def _persist_recovery_outputs(conn, memo_id, memo, pdf_path, appendix_path, selected, usage):
    """Persist every regenerated artifact and measured Capo token count together."""
    usage = usage if isinstance(usage, dict) else {}
    tokens_in = usage.get("input_tokens")
    tokens_out = usage.get("output_tokens")
    conn.execute(
        "UPDATE memos SET full_markdown=?, pdf_path=?, appendix_path=?, output_language=?, "
        "capo_tokens_in=?, capo_tokens_out=? WHERE id=?",
        (memo, pdf_path, appendix_path, selected, tokens_in, tokens_out, memo_id))


@scoped_language
def _regenerate(db, memo_id, rows, bb, *, memo_sha256=None, portfolio_snapshot=None):
    raise ValueError("Helper legacy disabilitato: usa main(memo_id=..., delivery_only=True) "
                     "o la ripresa nativa esplicitamente autorizzata; nessun mix con dati attuali")
    selected = capture_language()
    found = {}
    for r in rows:
        bb.data.setdefault(r["specialist"], {})[r["round_n"]] = r["content"]
        found[r["specialist"]] = r["round_n"]
    _log(f"Memo #{memo_id}: recuperati {len(found)} specialisti -> {sorted(found)}")

    if portfolio_snapshot is None:
        raise ValueError("Contesto portfolio storico assente: vietato mescolare report salvati e book corrente")
    portfolio = portfolio_snapshot

    # rischio + sizing (come consigliere_multi)
    risk_data = None
    try:
        from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
        risk_data = compute_portfolio_risk()
        if isinstance(risk_data, dict) and risk_data.get("error"):
            risk_data = None
    except Exception as e:
        _log("risk skip: " + str(e))

    sizing_context = None
    _stress = None   # letto anche dal punteggio quant (fix score 09/10)
    try:
        from bellomberg.portfolio.sizing_engine import compute_sizing, format_for_capo
        _stress = None
        try:  # replay GFC per budget #187 (best-effort, buco dichiarato se manca)
            from bellomberg.portfolio.portfolio_montecarlo import run_monte_carlo
            _stress = run_monte_carlo(horizon_days=252, n_sims=1000, method="block_bootstrap",
                                      stress_scenario="gfc_2008", seed=7)
            if isinstance(_stress, dict) and _stress.get("error"):
                _stress = None
        except Exception as _se:
            _log("replay GFC per budget skip: " + str(_se))
        _sz = compute_sizing(portfolio, risk_data, stress_data=_stress)
        if _sz and not _sz.get("error"):
            sizing_context = format_for_capo(_sz); bb.data["_sizing"] = _sz
    except Exception as e:
        _log("sizing skip: " + str(e))

    # scoreboard: TUTTI e 6 i domini vivi, come in una run vera del comitato.
    # Prima se ne calcolavano 4 ("solo gli scorer veloci"): il cruscotto del memo
    # recuperato mostrava 4 domini su 6 senza dire che mancavano gli altri due.
    # Costo misurato dei due aggiunti: options ~6s + fundamentals ~4s = ~10s su un
    # recupero che ne dura decine: trascurabile.
    scoring_context = None
    try:
        import bellomberg.agents.specialist_scores as S
        # v2 10/10 (Opus 5.5, riserva ALTO 1c): la cache degli score della RUN (snapshot del
        # blackboard ripristinato) e' la misura che il comitato ha visto: si riusa. Ricalcolare
        # oggi rimetterebbe lo score fundamentals su quote invecchiate (n.d.) e su un book
        # diverso. NB: questo helper legacy e' disabilitato (raise in testa); la ripresa vera
        # (run_multi_agent resume) ripristina gia' bb.data["_score_cache"] dallo snapshot.
        cache = dict(bb.data.get("_score_cache") or {})
        # fix score 09/10 (Opus 5.5): il rischio book vuole coda (replay GFC gia' calcolato
        # sopra per il sizing), mandato e cluster; un guasto passa come {"error"} dichiarato
        try:
            from bellomberg.core import mandato_pm as _mp
            _mandato_q = _mp.carica()
        except Exception as _me:
            _mandato_q = {"error": type(_me).__name__ + ": " + str(_me)[:160]}
        try:
            from bellomberg.portfolio.portfolio_sectors import compute_sector_exposure
            _settori_q = compute_sector_exposure(summary=portfolio)
        except Exception as _xe:
            _settori_q = {"error": type(_xe).__name__ + ": " + str(_xe)[:160]}
        _stress_q = _stress if _stress is not None else {"error": "replay GFC non disponibile in questo recupero"}
        for name, fn in [("quant", lambda: S.quant_score(portfolio, risk_data, stress_data=_stress_q,
                                                         mandato=_mandato_q, sector_data=_settori_q)),
                         ("macro", S.macro_score), ("crypto", S.crypto_score),
                         ("eventdesk", lambda: S.eventdesk_score(portfolio)),
                         ("fundamentals", lambda: S.fundamentals_score(portfolio)),
                         ("options", lambda: S.options_score(portfolio_data=portfolio))]:
            if cache.get(name):
                continue   # score della run: non ricalcolato
            try:
                sc = fn()
                if sc: cache[name] = sc
            except Exception as e:
                # audit/11 §5: uno scorer che crasha non deve sparire in silenzio dallo
                # scoreboard del Capo (stesso pattern del try esterno)
                _log(name + " score skip: " + str(e))
        if cache:
            bb.data["_score_cache"] = cache
            scoring_context = S.format_scoreboard(cache)
    except Exception as e:
        _log("scoreboard skip: " + str(e))

    _log("CAPO synthesis (recupero)...")
    memo, usage = run_capo(bb, portfolio_data=portfolio, memory_db=db,
                           sizing_context=sizing_context, scoring_context=scoring_context)
    from bellomberg.agents.weekly_lifecycle import validate_memo
    validate_memo(memo, usage)

    # MEMO LINTER (audit/07 P2, v1 solo flag) — stessa passata del run principale
    try:
        from bellomberg.reporting.memo_linter import build_linter_block
        _lblock = build_linter_block(memo, portfolio)
        if _lblock:
            memo = memo + "\n\n" + _lblock
            _log("MEMO LINTER: " + str(_lblock.count("\n- ")) + " avvertimenti aggiunti al memo")
    except Exception as e:
        _log("MEMO LINTER skipped: " + str(e))

    # ACTION VALIDATOR #191 (v1 solo flag) — stessa passata del run principale
    try:
        from bellomberg.agents.action_validator import build_validator_block
        _vblock = build_validator_block(memo, bb.data.get("_sizing"), db, exclude_memo_id=memo_id)
        if _vblock:
            memo = memo + "\n\n" + _vblock
            _log("ACTION VALIDATOR: " + str(_vblock.count("\n- ")) + " avvertimenti aggiunti al memo")
        # #204b HARD (Lotto C, ok PM 23/07; detect/apply separati come nel run
        # principale): qui la DETECT (blocco nel memo), l'APPLY dopo la ri-estrazione.
        from bellomberg.agents.action_validator import detect_sanity_exclusions
        _xblock, _sanity_pairs = detect_sanity_exclusions(memo)
        if _xblock:
            memo = memo + "\n\n" + _xblock
            _log("ACTION VALIDATOR: " + str(_xblock.count("\n- ")) + " righe su modelli BLOCK dichiarate")
    except Exception as e:
        _log("ACTION VALIDATOR skipped: " + str(e))
        _sanity_pairs = []

    os.makedirs(RESEARCH_NOTES_DIR, exist_ok=True)
    md_path = os.path.join(str(RESEARCH_NOTES_DIR), "RECOVERED_" + datetime.now().strftime("%Y%m%d_%H%M") + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(memo)
    _log("Markdown: " + md_path)

    # PDF istituzionale
    try:
        from bellomberg.portfolio.portfolio_analytics import compute_nav_history
        nav = compute_nav_history()
        if isinstance(nav, dict) and nav.get("error"): nav = None
    except Exception:
        nav = None
    pdf_path = None
    try:
        from bellomberg.reporting.pdf_institutional import build_institutional_memo
        pdf_path = build_institutional_memo(
            memo_markdown=memo, portfolio_data=portfolio, risk_data=risk_data,
            nav_history=nav, sizing_data=bb.data.get("_sizing"),
            scoring_data=bb.data.get("_score_cache"))
        _log("PDF MEMO: " + str(pdf_path))
    except Exception as e:
        _log("PDF memo error: " + str(e))

    # appendice quant
    appendix_path = None
    try:
        from bellomberg.reporting.charts_quant import build_quant_appendix_v2
        ap = build_quant_appendix_v2(blackboard=bb, portfolio_data=portfolio,
                                     macro_data=None, options_data_dict={}, correlation_data=None)
        _log("PDF APPENDICE: " + str(ap))
        appendix_path = ap
    except Exception as e:
        _log("appendix error: " + str(e))

    from bellomberg.agents.weekly_lifecycle import file_receipt
    file_receipt(pdf_path, kind="PDF")
    # Prepare first; memo and replacement proposals commit in one transaction.
    db.replace_memo_decisions(memo_id, memo,
        update_memo=lambda conn: _persist_recovery_outputs(
            conn, memo_id, memo, pdf_path, appendix_path, selected, usage))
    if _sanity_pairs:
        from bellomberg.agents.action_validator import apply_sanity_exclusions
        apply_sanity_exclusions(db, memo_id, _sanity_pairs)

    # Recovery uses the exact saved receipt for this memo. Recent files on disk
    # do not identify a run and may belong to another memo or be personal copies.
    try:
        from bellomberg.reporting.email_sender import email_configurata, invia_email_multi_allegati
        from bellomberg.reporting.valuation_delivery import (
            recover_manifest, record_email_outcome, save_manifest)
        delivery = recover_manifest(memo_id, receipts_dir=RESEARCH_NOTES_DIR,
                                    roots=[MODELS_DIR, REPORT_DIR], memo_sha256=memo_sha256)
        delivery_path = md_path.replace(".md", "_valuations.json")
        save_manifest(delivery_path, delivery)
        attachments = []
        if pdf_path:
            attachments.append(str(pdf_path))
        if appendix_path:
            attachments.append(str(appendix_path))
        attachments.extend(delivery["attachments"])
        body_extra = text(
            "<p>Memo rigenerato dai report salvati (recupero). Excel allegati solo se "
            "attestati dal receipt del medesimo memo e ricontrollati per hash; "
            "gli altri modelli non sono allegati.</p>",
            "<p>Memo regenerated from saved reports (recovery). Excel files are attached "
            "only when attested by this memo's receipt and rechecked by hash; "
            "other models are excluded.</p>")
        if not delivery["attachments"]:
            body_extra += text("<p>Nessun Excel attestabile per questo memo.</p>",
                               "<p>No verifiable Excel for this memo.</p>")
        sent = False
        if email_configurata():
            sent = invia_email_multi_allegati(
                pdf_paths=attachments,
                oggetto=text("[BELLOMBERG] Ricerca settimanale (RECUPERO) - ",
                             "[BELLOMBERG] Weekly Research (RECOVERY) - ") + datetime.now().strftime("%d/%m/%Y"),
                body_extra=body_extra,
                expected_hashes=delivery["expected_hashes"], delivery_receipt=delivery)
            _log("Email recupero " + ("inviata" if sent else "NON inviata") +
                 " (" + str(len(attachments)) + " allegati)")
        else:
            _log("[!] Email non configurata: allegati pronti ma NON inviati -> " +
                 "; ".join(attachments))
        record_email_outcome(delivery, sent)
        save_manifest(delivery_path, delivery)
    except Exception as e:
        _log("[!] Email recupero error: " + str(e))

    bb.mark_run_complete()
    _log("RECUPERO COMPLETO. Memo #" + str(memo_id) + " rigenerato dai report salvati.")
    print("\nNB: manca il contributo dell'agente che si era bloccato; tutto il resto c'e'.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Recover an explicitly selected weekly memo")
    parser.add_argument("--language", choices=("it", "en"))
    parser.add_argument("--memo-id", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--delivery-only", action="store_true")
    mode.add_argument("--authorize-new-ai", action="store_true")
    parser.add_argument("--send-email", action="store_true")
    args = parser.parse_args()
    outcome = main(language=args.language, memo_id=args.memo_id, delivery_only=args.delivery_only,
                   authorize_new_ai=args.authorize_new_ai, send_email=args.send_email)
    print("Memo #" + str(args.memo_id) + ": " + str((outcome or {}).get("status", "unavailable")))
    if (outcome or {}).get("blocked_reason"):
        print(outcome["blocked_reason"])
    if (outcome or {}).get("status") != "completed":
        raise SystemExit(2)
