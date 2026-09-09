"""
action_validator.py — ACTION VALIDATOR #191 (v1, SOLO FLAG — scelta PM 13/07).

Valida ogni riga della ACTION TABLE del Capo PRIMA della generazione PDF e
produce un blocco markdown di avvertimenti da appendere al memo. NON tocca i
numeri del Capo (niente clip): il PM resta l'unico che decide.

Controlli per riga:
  1. SIZING — BUY/ADD oltre lo spazio residuo del sizing engine (posizioni
     esistenti: remaining_capacity_eur; nomi nuovi: limite base per nome).
  2. RIPROPOSTE — stessa azione sullo stesso ticker già proposta in memo
     precedenti e mai eseguita (status PENDING nel DB): il contatore rende
     visibile il riciclo (audit 13/07: BSX proposta 4 volte come "nuova").
  3. IGIENE — azione non standard o ticker assente dal book su TRIM/SELL.
  4. FEEDBACK PM — sul ticker esiste un feedback diretto del PM su decisioni
     passate (21/07, lezione memo #46: il veto "basta proporre covered call" su
     MSTR #186 era SKIPPED, non PENDING, e il check 2 non lo vedeva). Passata
     separata su TUTTE le righe (HOLD comprese), una voce per ticker, ultimi 2
     feedback. Il codice non interpreta il testo: lo CITA, e il PM giudica.

Il parser della tabella è una copia self-contained di quello di
memory_db.extract_and_save_decisions (stesso precedente di fix_decisions_eur.py):
se cambia il formato della ACTION TABLE vanno aggiornati entrambi.
"""
import json
import os
import re
from datetime import datetime

# 21/08: la policy sulle parole del PM sta in UN posto solo. L'import e' QUI e non
# dentro la funzione perche' il blocco FEEDBACK PM e' avvolto da un
# `except Exception: pass`: un import fallito la' dentro avrebbe fatto sparire in
# silenzio TUTTI gli avvertimenti sui feedback del PM dal memo. Nessuna
# circolarita': memory_db non importa action_validator (verificato).
from bellomberg.storage.memory_db import pm_verbatim

ACTIONS_KNOWN = {"BUY", "ADD", "SELL", "TRIM", "HOLD", "RESEARCH", "HEDGE", "WATCH"}
ACTIONS_SKIP_CHECKS = {"HOLD"}          # ripetere HOLD su un book statico e' fisiologico
SIZING_TOLERANCE_EUR = 500.0            # sotto questa soglia lo sforo non fa rumore


def _parse_action_rows(memo_markdown):
    """Copia self-contained del parse di memory_db.extract_and_save_decisions."""
    from bellomberg.storage.memory_db import _parse_eur_amount
    m = re.search(r"##\s*ACTION TABLE.*?\n(\|.*?\|.*?\n)+",
                  memo_markdown, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    rows = []
    for line in m.group(0).split("\n"):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or "---" in cells[0] or cells[0].lower() in ("action",):
            continue
        if len(cells) < 5:
            continue
        rows.append({"action": cells[0].upper().strip(),
                     "ticker": cells[1].upper().strip(),
                     "eur": _parse_eur_amount(cells[2]),
                     # banda con deroga (15/07): serve la riga intera per vedere
                     # se il Capo ha dichiarato 'SOPRA POLICY' nella motivazione
                     "raw": line})
    return rows


def _ddmm(iso):
    s = str(iso or "")
    return (s[8:10] + "/" + s[5:7]) if len(s) >= 10 else "?"


def build_validator_block(memo_markdown, sizing, db, exclude_memo_id=None):
    """Ritorna il blocco markdown '## ACTION VALIDATOR' (o stringa vuota se pulito).
    Non solleva mai: ogni check e' guarded, un guasto del validator non deve
    toccare la run (il chiamante ha comunque il suo try/except)."""
    rows = _parse_action_rows(memo_markdown)
    if not rows:
        return ""

    warnings = []
    controlli_db_falliti = set()

    # --- contesto sizing (posizioni esistenti + limite base per nomi nuovi) ---
    sz_pos, invested, base_single_pct = {}, 0.0, None
    if isinstance(sizing, dict) and not sizing.get("error"):
        for p in (sizing.get("positions") or []):
            if p.get("ticker"):
                sz_pos[str(p["ticker"]).upper()] = p
        summ = sizing.get("summary") or {}
        invested = float(summ.get("invested_capital_eur") or 0.0)
        base_single_pct = (summ.get("params") or {}).get("single_base_pct")

    # 06/09 (lotto B, difetto 1 della coda di A2, assegnato dal PM): un cancello che non ha
    # POTUTO controllare deve dirlo. Da quando i tetti vengono dal mandato (A2), un mandato
    # assente fa tornare al motore `{"error": ...}`: fino a oggi questo ramo lasciava
    # `sz_pos` vuoto e `base_single_pct` a None, i controlli 1 e 3b non avevano su cosa
    # girare, e piu' sotto `if not warnings: return ""` faceva uscire il memo PULITO.
    # «Non ho guardato» non e' «non c'e' niente da dire» (regola PM 14/07).
    causa_sizing = None
    if sizing is None:
        causa_sizing = "il motore di sizing non ha prodotto nulla"
    elif not isinstance(sizing, dict):
        causa_sizing = "contesto di sizing inatteso (%s)" % type(sizing).__name__
    elif sizing.get("error"):
        causa_sizing = str(sizing.get("error"))

    _compere = [r for r in rows if r["action"] in ("BUY", "ADD") and r["ticker"] and r["eur"]]
    _vendite = [r for r in rows if r["action"] in ("TRIM", "SELL") and r["ticker"]]
    if causa_sizing:
        # Si dichiara il LAVORO non fatto, non il dato mancante: senza righe da controllare
        # non c'e' nessun controllo saltato, e un avviso su ogni memo smetterebbe di dire
        # qualcosa.
        _quali = []
        if _compere:
            _quali.append("gli acquisti (BUY/ADD) NON sono stati confrontati con i limiti di sizing")
        if _vendite:
            _quali.append("le vendite (TRIM/SELL) NON sono state confrontate col book del sizing")
        if _quali:
            warnings.append(
                "**CONTROLLO SIZING NON ESEGUITO** — " + "; ".join(_quali) +
                f". Causa: {causa_sizing}. Le righe qui sotto non sono passate dalla policy "
                "di sizing: vanno verificate a mano prima di eseguirle")
    elif _compere and not base_single_pct:
        _nuovi = sorted({r["ticker"] for r in _compere if r["ticker"] not in sz_pos})
        if _nuovi:
            warnings.append(
                f"**CONTROLLO SIZING NON ESEGUITO sui nomi nuovi** ({', '.join(_nuovi)}): il "
                "sizing non porta il limite base per nome (single_base_pct), quindi non c'e' "
                "niente con cui confrontarli")

    for r in rows:
        act, tk, eur = r["action"], r["ticker"], r["eur"]
        if not tk or act in ACTIONS_SKIP_CHECKS:
            continue

        # 3a. azione non standard (il parser la salva comunque: solo avviso)
        if act not in ACTIONS_KNOWN:
            warnings.append(f"**{tk}**: azione '{act}' non standard (il parser potrebbe interpretarla male)")

        # 1. sizing (solo BUY/ADD con importo) — banda con deroga dichiarata (15/07,
        # scelta PM): sopra policy CON tag 'SOPRA POLICY' in riga = nota informativa
        # (deroga consapevole del comitato); sopra policy SENZA tag = violazione flaggata.
        if act in ("BUY", "ADD") and eur:
            declared = "SOPRA POLICY" in (r.get("raw") or "").upper()
            if tk in sz_pos:
                room = float(sz_pos[tk].get("remaining_capacity_eur") or 0.0)
                if eur > room + SIZING_TOLERANCE_EUR:
                    if declared:
                        warnings.append(
                            f"**{act} {tk} {eur:,.0f}€**: DEROGA DICHIARATA sopra la policy "
                            f"(spazio policy ~{room:,.0f}€) — valuta la motivazione del comitato")
                    else:
                        warnings.append(
                            f"**{act} {tk} {eur:,.0f}€**: oltre la policy di sizing "
                            f"(~{room:,.0f}€ — {sz_pos[tk].get('verdict', '')}) e deroga NON "
                            "dichiarata (manca 'SOPRA POLICY' + motivazione in riga)")
            elif invested > 0 and base_single_pct:
                max_new = invested * float(base_single_pct) / 100.0
                if eur > max_new + SIZING_TOLERANCE_EUR:
                    if declared:
                        warnings.append(
                            f"**{act} {tk} {eur:,.0f}€** (nome nuovo): DEROGA DICHIARATA sopra "
                            f"la policy base (~{max_new:,.0f}€ = {base_single_pct:.0f}% "
                            "dell'investito) — valuta la motivazione del comitato")
                    else:
                        warnings.append(
                            f"**{act} {tk} {eur:,.0f}€** (nome nuovo): sopra la policy base per "
                            f"nome (~{max_new:,.0f}€ = {base_single_pct:.0f}% dell'investito, "
                            "prima degli aggiustamenti vol/correlazione) e deroga NON dichiarata")

        # 3b. TRIM/SELL su ticker non in book (per il sizing engine)
        if act in ("TRIM", "SELL") and sz_pos and tk not in sz_pos:
            warnings.append(f"**{act} {tk}**: ticker non presente tra le posizioni del sizing engine")

        # 2. riproposte mai eseguite (SELECT read-only sul DB decisioni)
        if act not in ("HOLD",) and db is not None:
            try:
                same = {"BUY": ("BUY", "ADD"), "ADD": ("BUY", "ADD"),
                        "SELL": ("SELL", "TRIM"), "TRIM": ("SELL", "TRIM")}.get(act, (act,))
                ph = ",".join("?" * len(same))
                q = (f"SELECT COUNT(id), MIN(memo_id), MIN(timestamp) FROM decisions "
                     f"WHERE UPPER(ticker)=? AND UPPER(action) IN ({ph}) AND status='PENDING'")
                params = [tk, *same]
                if exclude_memo_id is not None:
                    q += " AND memo_id != ?"
                    params.append(exclude_memo_id)
                with db._conn() as conn:
                    row = conn.execute(q, params).fetchone()
                n_prev = int(row[0] or 0)
                if n_prev >= 1:
                    volte = "1 volta" if n_prev == 1 else f"{n_prev} volte"
                    warnings.append(
                        f"**{act} {tk}**: già proposta {volte} senza esecuzione "
                        f"(prima nel memo #{row[1]} del {_ddmm(row[2])}, status PENDING) — "
                        "eseguirla, archiviarla o spiegare perché viene riproposta")
            except Exception as e:
                if "riproposte" not in controlli_db_falliti:
                    controlli_db_falliti.add("riproposte")
                    warnings.append("**CONTROLLO RIPROPOSTE NON ESEGUITO** — registro decisioni "
                                    "non leggibile (%s: %s)" % (type(e).__name__, str(e)[:120]))

    # 4. feedback PM registrato sul ticker (21/07, lezione memo #46): qualunque
    # status — il veto #186 era SKIPPED e il check 2 (solo PENDING) non lo vedeva.
    # Review 21/07: passata SEPARATA su TUTTE le righe (anche HOLD: il veto #186
    # stava proprio su una riga HOLD), UNA voce per ticker (niente doppioni) e
    # ULTIMI 2 feedback (il piu' recente non maschera un veto precedente).
    if db is not None:
        _seen_fb = set()
        for r in rows:
            tk = r["ticker"]
            if not tk or tk in _seen_fb:
                continue
            _seen_fb.add(tk)
            try:
                q = ("SELECT id, action, memo_id, pm_feedback FROM decisions "
                     "WHERE UPPER(ticker)=? AND pm_feedback IS NOT NULL "
                     "AND TRIM(pm_feedback) != ''")
                params = [tk]
                if exclude_memo_id is not None:
                    q += " AND memo_id != ?"
                    params.append(exclude_memo_id)
                q += " ORDER BY id DESC LIMIT 2"
                with db._conn() as conn:
                    fb_rows = conn.execute(q, params).fetchall()
                if fb_rows:
                    # 21/08: era `str(x[3])[:120] + "»"` — taglio a un letterale col
                    # caporale di CHIUSURA rimesso dopo, dentro un blocco che dice
                    # «citate testualmente». Stessa policy di memory_db, un posto solo.
                    quotes = "; ".join(
                        "#" + str(x[0]) + " " + str(x[1]) + " (memo #" + str(x[2]) + "): "
                        + pm_verbatim(x[3], x[0], virgolette=True) for x in fb_rows)
                    warnings.append(
                        f"**{tk}**: feedback DIRETTO del PM su decisioni passate — {quotes} — "
                        "verificare che la proposta non li contraddica o che il memo "
                        "dichiari i fatti nuovi")
            except Exception as e:
                if "feedback" not in controlli_db_falliti:
                    controlli_db_falliti.add("feedback")
                    warnings.append("**CONTROLLO FEEDBACK PM NON ESEGUITO** — registro decisioni "
                                    "non leggibile (%s: %s)" % (type(e).__name__, str(e)[:120]))

    if not warnings:
        return ""
    block = ["## ACTION VALIDATOR (verifica automatica #191 — flag-only, i numeri del Capo NON sono stati modificati)"]
    block += [f"- {w}" for w in warnings]
    block.append(f"*(validator v1 — {datetime.now().strftime('%d/%m %H:%M')}; "
                 "sizing = motore vol×corr; riproposte = decisioni PENDING nei memo "
                 "precedenti; feedback = parole del PM sulle decisioni passate, citate testualmente)*")
    return "\n".join(block)


def _canonical_sanity(ticker, report_dir=None):
    """Sanity del modello corrente di un ticker: legge il sidecar canonico
    `VAL_X.payload.json` **oppure** `VAL_X_FLAGGED.payload.json` — mutuamente
    esclusivi per costruzione (dcf_engine._write_payload_sidecar rimuove la
    variante opposta). Review Lotto C F2: i modelli BLOCK vivono SOLO nel
    sidecar _FLAGGED (misurati 6/6 nel report/ reale) — guardare solo il
    canonico rendeva l'esclusione morta per costruzione.
    Ritorna (severity, judged_at) — (None, None) = sidecar assente/illeggibile:
    MAI escludere per assenza di dato."""
    try:
        from bellomberg.core.paths import REPORT_DIR
        d = report_dir or str(REPORT_DIR)
        base = "VAL_" + str(ticker).replace(".", "_")
        for name in (base + "_FLAGGED.payload.json", base + ".payload.json"):
            p = os.path.join(d, name)
            if not os.path.exists(p):
                continue
            with open(p, "r", encoding="utf-8") as f:
                payload = json.load(f)
            sev = ((payload.get("sanity") or {}).get("severity") or "").upper() or None
            judged_at = str(payload.get("_timestamp") or "")[:10] or None
            return sev, judged_at
    except Exception:
        pass
    return None, None


def detect_sanity_exclusions(memo_markdown, report_dir=None):
    """#204b residuo — ESCLUSIONE HARD, fase DETECT (pacchetto verita' dei
    numeri Lotto C, ok PM 23/07; riprogettata dopo review: detect/apply separati
    perche' il memo si finalizza PRIMA che le decisioni siano salvate).
    Righe BUY/ADD della ACTION TABLE su ticker col modello corrente in sanity
    **BLOCK** → blocco markdown dichiarato + lista pairs per la fase APPLY.
    Azioni non-standard su ticker BLOCK: il gate automatico NON le copre e lo
    DICE (review F4 — mai bypass muto). Solo BUY/ADD chiudono: un TRIM/SELL su
    modello rotto resta decisione del PM.
    Ritorna (block_markdown, pairs) — ('' , []) se nulla. Non solleva mai."""
    try:
        rows = _parse_action_rows(memo_markdown)
    except Exception:
        return "", []
    lines, pairs = [], []
    for r in rows:
        act, tk = r.get("action"), r.get("ticker")
        if not tk:
            continue
        sev, judged_at = _canonical_sanity(tk, report_dir)
        if sev != "BLOCK":
            continue
        vintage = f" (giudizio sanity del {judged_at})" if judged_at else " (data giudizio n.d.)"
        if act in ("BUY", "ADD"):
            pairs.append((act, tk))
            lines.append(
                f"**{act} {tk}**: modello corrente in **sanity BLOCK**{vintage} → riga NON "
                "azionabile: la decisione viene auto-chiusa nel registro (SKIPPED + nota "
                "AUTO-ESCLUSA) al salvataggio. Per riproporla serve un modello rivalidato "
                "(variant view / rigenerazione)")
        elif act not in ACTIONS_KNOWN:
            lines.append(
                f"**{act} {tk}**: azione NON standard su modello in sanity BLOCK{vintage} — "
                "il gate automatico copre solo BUY/ADD: VALUTARE A MANO (dichiarato, non muto)")
    if not lines:
        return "", []
    block = ["## ACTION VALIDATOR — ESCLUSIONI HARD (sanity BLOCK, #204b)"]
    block += [f"- {ln}" for ln in lines]
    block.append("*(le righe restano nel memo per storia vera; l'esito dell'auto-chiusura "
                 "e' nel log della run — se una riga non viene trovata nel registro, il log lo dichiara)*")
    return "\n".join(block), pairs


def apply_sanity_exclusions(db, memo_id, pairs):
    """#204b HARD, fase APPLY: da chiamare DOPO extract_and_save_decisions.
    Chiude le decisioni PENDING del memo per i pairs rilevati: status SKIPPED +
    outcome_notes AUTO-ESCLUSA + closed_at — storia CONSERVATA, mai cancellata.
    Match su UPPER(action) IN ('BUY','ADD') qualunque fosse la riga del memo
    (review F3: la guardia book-aware salva una BUY su posizione esistente come
    ADD). NB dichiarato: EXCLUDED_SANITY letterale non e' ammesso dal CHECK di
    decisions.status → SKIPPED + nota = stessa semantica.
    Ritorna il numero di decisioni chiuse. Non solleva mai."""
    if not pairs or db is None or memo_id is None:
        return 0
    n_upd = 0
    now = datetime.now().isoformat(timespec="seconds")
    for _act, tk in pairs:
        try:
            with db._conn() as conn:
                cur = conn.execute(
                    "UPDATE decisions SET status='SKIPPED', closed_at=?, "
                    "outcome_notes=COALESCE(outcome_notes,'') || ? "
                    "WHERE memo_id=? AND UPPER(ticker)=? "
                    "AND UPPER(action) IN ('BUY','ADD') AND status='PENDING'",
                    (now, "[AUTO-ESCLUSA " + now[:10] + ": modello in sanity BLOCK — "
                          "riga non azionabile, #204b hard] ",
                     memo_id, tk))
                n_upd += cur.rowcount
        except Exception:
            pass
    return n_upd


if __name__ == "__main__":
    # Test standalone read-only sull'ultimo memo reale
    from bellomberg.storage.memory_db import MemoryDB
    db = MemoryDB()
    with db._conn() as conn:
        memo_id, md = conn.execute(
            "SELECT id, full_markdown FROM memos WHERE LENGTH(full_markdown) > 1000 "
            "ORDER BY id DESC LIMIT 1").fetchone()
    print(f"Test su memo #{memo_id} ({len(md)} char)")
    sizing = None
    try:
        from bellomberg.portfolio.sizing_engine import compute_sizing
        sizing = compute_sizing(db.get_portfolio_summary())
    except Exception as e:
        print("[test] sizing non disponibile:", e)
    print(build_validator_block(md, sizing, db, exclude_memo_id=memo_id) or "(nessun avvertimento)")
