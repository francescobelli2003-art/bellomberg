"""
regenerate_memo.py — RECUPERO: rigenera il memo dai report degli specialisti GIA'
salvati nel DB (Round 2 persistiti), senza rifare i 3 round. Utile quando una run
si impalla su un agente: si recupera il lavoro gia' pagato facendo partire SOLO il Capo.
Costo: solo la chiamata del Capo (+ qualche scorer veloce), non un'altra run intera.

USO:  python regenerate_memo.py
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


def _log(m): print("[" + datetime.now().strftime("%H:%M:%S") + "] " + m)


def main():
    db = MemoryDB()
    with db._conn() as conn:
        memo = conn.execute("SELECT id FROM memos ORDER BY id DESC LIMIT 1").fetchone()
        if not memo:
            print("Nessun memo nel DB."); return
        memo_id = memo["id"]
        rows = conn.execute(
            "SELECT specialist, round_n, content FROM specialist_reports "
            "WHERE memo_id=? ORDER BY round_n", (memo_id,)).fetchall()
    if not rows:
        print(f"Nessun report salvato per memo #{memo_id}: niente da recuperare "
              f"(la run si e' bloccata prima di finalizzare i Round 2)."); return

    # 05/09 (criterio 5, review): senza mandato run_capo solleva MandatoMancante — dillo QUI, prima
    # del Blackboard (che scrive l'heartbeat di F4) e dei minuti di risk/sizing, non alla fine.
    from bellomberg.core import mandato_pm
    try:
        mandato_pm.carica()
    except mandato_pm.MandatoMancante as _mm:
        _log("MANDATO NON DICHIARATO: " + str(_mm))
        _log("Il recupero del memo non parte senza il mandato del PM: compila la pagina Mandato (F11) e rilancia.")
        raise SystemExit(2)
    bb = Blackboard(memory_db=db, memo_id=memo_id)
    found = {}
    for r in rows:
        bb.data.setdefault(r["specialist"], {})[r["round_n"]] = r["content"]
        found[r["specialist"]] = r["round_n"]
    _log(f"Memo #{memo_id}: recuperati {len(found)} specialisti -> {sorted(found)}")

    portfolio = db.get_portfolio_summary()

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
        cache = {}
        for name, fn in [("quant", lambda: S.quant_score(portfolio, risk_data)),
                         ("macro", S.macro_score), ("crypto", S.crypto_score),
                         ("eventdesk", lambda: S.eventdesk_score(portfolio)),
                         ("fundamentals", lambda: S.fundamentals_score(portfolio)),
                         ("options", lambda: S.options_score(portfolio_data=portfolio))]:
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

    # update memo nel DB + email
    try:
        with db._conn() as conn:
            conn.execute("UPDATE memos SET full_markdown=?, pdf_path=? WHERE id=?",
                         (memo, pdf_path, memo_id))
            # idempotente: cancella le decisioni gia' estratte per questo memo prima di ri-estrarre
            conn.execute("DELETE FROM decisions WHERE memo_id=? AND status='PENDING'", (memo_id,))
            # #204b HARD (review Lotto C, F1c): via anche le auto-chiusure di codice
            # dello STESSO memo — senza questo ogni regenerate ne creava un
            # duplicato fantasma (la ri-estrazione reinserisce la riga, l'apply la
            # richiude). Tocca SOLO righe generate dal codice e mai annotate dal PM.
            conn.execute("DELETE FROM decisions WHERE memo_id=? AND status='SKIPPED' "
                         "AND outcome_notes LIKE '%AUTO-ESCLUSA%' "
                         "AND (pm_feedback IS NULL OR TRIM(pm_feedback)='')", (memo_id,))
    except Exception as e:
        _log("DB update skip: " + str(e))
    else:
        # audit/11 §4: il DELETE sopra e' gia' COMMITTATO — se la ri-estrazione fallisce
        # le PENDING sono perse in silenzio. Ora l'errore URLA e dice come recuperare.
        try:
            db.extract_and_save_decisions(memo_id, memo)
        except Exception as e:
            _log(f"[!] ATTENZIONE: PENDING del memo {memo_id} CANCELLATE ma ri-estrazione "
                 f"FALLITA ({e}): rilancia regenerate_memo.py per ricrearle dall'ACTION TABLE")
        else:
            # #204b HARD fase APPLY (dopo la ri-estrazione: le righe esistono ora)
            try:
                if _sanity_pairs:
                    from bellomberg.agents.action_validator import apply_sanity_exclusions
                    _n_x = apply_sanity_exclusions(db, memo_id, _sanity_pairs)
                    _log("ACTION VALIDATOR: esclusioni HARD applicate: "
                         + str(_n_x) + "/" + str(len(_sanity_pairs)))
            except Exception as _xe:
                _log("[!] esclusioni HARD non applicate: " + str(_xe))

    # EMAIL DI RECUPERO COMPLETA (#31): replica la run normale (consigliere_multi) -
    # memo PDF + appendice quant + modelli Excel VAL_/DCF_ delle ultime 48h.
    # Prima il recupero non inviava nulla: il PM riceveva il memo monco senza allegati.
    try:
        import glob, time
        from bellomberg.reporting.email_sender import email_configurata, invia_email_multi_allegati
        attachments = []
        if pdf_path:
            attachments.append(str(pdf_path))
        if appendix_path:
            attachments.append(str(appendix_path))
        cutoff = time.time() - 48 * 3600
        for pat in (os.path.join(str(MODELS_DIR), "DCF_*.xlsx"),
                    os.path.join(str(MODELS_DIR), "VAL_*.xlsx"),
                    os.path.join(str(REPORT_DIR), "VAL_*.xlsx")):
            for p in sorted(glob.glob(pat)):
                try:
                    if os.path.getmtime(p) >= cutoff:
                        attachments.append(p)
                except Exception:
                    continue
        seen = set()
        attachments = [a for a in attachments if a and not (a in seen or seen.add(a))]
        if email_configurata():
            ok = invia_email_multi_allegati(
                pdf_paths=attachments,
                oggetto="[BELLOMBERG] Weekly Research (RECUPERO) - " + datetime.now().strftime("%d/%m/%Y"),
                body_extra="<p>Memo rigenerato dai report salvati (recupero): in allegato memo, "
                           "appendice quant e modelli VAL/DCF generati nelle ultime 48 ore.</p>")
            _log("Email recupero " + ("inviata" if ok else "NON inviata") +
                 " (" + str(len(attachments)) + " allegati)")
        else:
            _log("[!] Email non configurata: allegati pronti ma NON inviati -> " +
                 "; ".join(attachments))
    except Exception as e:
        _log("[!] Email recupero error: " + str(e))

    bb.mark_run_complete()
    _log("RECUPERO COMPLETO. Memo #" + str(memo_id) + " rigenerato dai report salvati.")
    print("\nNB: manca il contributo dell'agente che si era bloccato; tutto il resto c'e'.")


if __name__ == "__main__":
    main()
