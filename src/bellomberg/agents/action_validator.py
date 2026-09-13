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
from datetime import datetime, timedelta

# 21/08: la policy sulle parole del PM sta in UN posto solo. L'import e' QUI e non
# dentro la funzione perche' il blocco FEEDBACK PM e' avvolto da un
# `except Exception: pass`: un import fallito la' dentro avrebbe fatto sparire in
# silenzio TUTTI gli avvertimenti sui feedback del PM dal memo. Nessuna
# circolarita': memory_db non importa action_validator (verificato).
from bellomberg.storage.memory_db import pm_verbatim
from bellomberg.core.language import (ACTION_TABLE_HEADERS, POLICY_OVERRIDE_MARKERS,
                                      NEW_FACT_MARKERS, text, scoped_language)

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
        if not cells or "---" in cells[0] or cells[0].lower() in ACTION_TABLE_HEADERS:
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


@scoped_language
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
        causa_sizing = text("il motore di sizing non ha prodotto nulla", "the sizing engine produced no result")
    elif not isinstance(sizing, dict):
        causa_sizing = text("contesto di sizing inatteso (%s)", "unexpected sizing context (%s)") % type(sizing).__name__
    elif sizing.get("error"):
        causa_sizing = str(sizing.get("error"))

    _compere = [r for r in rows if r["action"] in ("BUY", "ADD") and r["ticker"] and r["eur"]]
    _vendite = [r for r in rows if r["action"] in ("TRIM", "SELL") and r["ticker"]]

    # Audit 11/09 (Fable 5.1): con il budget di stress SFORATO e capacita' dispiegabile 0
    # il Capo poteva impiegare cash lo stesso. Sui nomi ESISTENTI il
    # flag c'era ma diceva «~0€ — spazio +X€ entro il limite» (due numeri che si
    # contraddicono nella stessa riga: il primo e' dopo il budget, il secondo prima); sul
    # nome NUOVO non c'era NESSUN flag: il confronto usava solo il 10% base per nome. Ora
    # il budget che VINCOLA e' un controllo suo, con la stessa deroga 'SOPRA POLICY'.
    _bud = {}
    _budget_vincola = False
    _bud_txt = ""
    if isinstance(sizing, dict) and not sizing.get("error"):
        _summ = sizing.get("summary") or {}
        _bud = _summ.get("stress_var_budget") if isinstance(_summ.get("stress_var_budget"), dict) else {}
        # la capacita' che il budget lascia (review 11/09: e' `additional_capacity_eur` del
        # budget, non il cash dispiegabile — con poco cash e budget libero il limite sarebbe
        # il cash, non il budget; senza il campo si ripiega sul dispiegabile, dichiarato)
        _cap_raw = _bud.get("additional_capacity_eur")
        if _cap_raw is None:
            _cap_raw = _summ.get("deployable_from_cash_eur")
        try:
            _disp = float(_cap_raw or 0.0)
        except (TypeError, ValueError):
            _disp = 0.0
        _budget_vincola = bool(_bud.get("binding")) and _disp <= SIZING_TOLERANCE_EUR
        # aggregato: la somma dei BUY/ADD contro la capacita' residua del budget, anche
        # quando il budget vincola ma non azzera (review 11/09, predicato 2a)
        try:
            _somma_compere = sum(float(r["eur"]) for r in _compere)
        except (TypeError, ValueError):
            _somma_compere = 0.0
        if (bool(_bud.get("binding")) and _bud.get("additional_capacity_eur") is not None
                and not _budget_vincola and _somma_compere > _disp + SIZING_TOLERANCE_EUR):
            warnings.append(
                text("**BUY/ADD in totale {:,.0f}€** contro una capacita' residua del budget di stress "
                "di ~{:,.0f}€ (budget VINCOLANTE, stato {}): la somma delle aggiunte supera cio' "
                "che il budget lascia", "**Total BUY/ADD {:,.0f}€** against remaining stress-budget capacity "
                "of ~{:,.0f}€ (BINDING budget, status {}): combined additions exceed the remaining budget"
                ).format(_somma_compere, _disp, _bud.get("gfc_status") or "n.d."))
        if _budget_vincola:
            _gfc = ""
            if _bud.get("gfc_replay_nav_pct") is not None and _bud.get("budget_gfc_replay_nav_pct") is not None:
                try:
                    _gfc = text(" replay GFC {:+.1f}% del NAV vs budget {:+.0f}%",
                                " GFC replay {:+.1f}% of NAV vs budget {:+.0f}%").format(
                        float(_bud["gfc_replay_nav_pct"]), float(_bud["budget_gfc_replay_nav_pct"]))
                except (TypeError, ValueError):
                    _gfc = ""
            _bud_txt = text("il budget di stress VINCOLA il sizing (capacita' dispiegabile ~{:,.0f}€, "
                           "stato {}{})", "the stress budget BINDS sizing (deployable capacity ~{:,.0f}€, "
                           "status {}{})").format(_disp, _bud.get("gfc_status") or "n.d.", _gfc)
    if causa_sizing:
        # Si dichiara il LAVORO non fatto, non il dato mancante: senza righe da controllare
        # non c'e' nessun controllo saltato, e un avviso su ogni memo smetterebbe di dire
        # qualcosa.
        _quali = []
        if _compere:
            _quali.append(text("gli acquisti (BUY/ADD) NON sono stati confrontati con i limiti di sizing",
                               "purchases (BUY/ADD) were NOT checked against sizing limits"))
        if _vendite:
            _quali.append(text("le vendite (TRIM/SELL) NON sono state confrontate col book del sizing",
                               "sales (TRIM/SELL) were NOT checked against the sizing book"))
        if _quali:
            warnings.append(
                text("**CONTROLLO SIZING NON ESEGUITO** — ", "**SIZING CHECK NOT PERFORMED** — ")
                + "; ".join(_quali) + text(". Causa: {}. Le righe qui sotto non sono passate dalla policy "
                "di sizing: vanno verificate a mano prima di eseguirle", ". Cause: {}. The rows below have not "
                "passed sizing-policy checks: verify them manually before execution").format(causa_sizing))
    elif _compere and not base_single_pct:
        _nuovi = sorted({r["ticker"] for r in _compere if r["ticker"] not in sz_pos})
        if _nuovi:
            warnings.append(
                text("**CONTROLLO SIZING NON ESEGUITO sui nomi nuovi** ({}): il "
                "sizing non porta il limite base per nome (single_base_pct), quindi non c'e' "
                "niente con cui confrontarli", "**SIZING CHECK NOT PERFORMED for new names** ({}): "
                "sizing provides no base per-name limit (single_base_pct), so no comparison is possible"
                ).format(', '.join(_nuovi)))

    for r in rows:
        act, tk, eur = r["action"], r["ticker"], r["eur"]
        if not tk or act in ACTIONS_SKIP_CHECKS:
            continue

        # 3a. azione non standard (il parser la salva comunque: solo avviso)
        if act not in ACTIONS_KNOWN:
            warnings.append(text("**{}**: azione '{}' non standard (il parser potrebbe interpretarla male)",
                                 "**{}**: non-standard action '{}' (the parser may misinterpret it)").format(tk, act))

        # 1. sizing (solo BUY/ADD con importo) — banda con deroga dichiarata (15/07,
        # scelta PM): sopra policy CON tag 'SOPRA POLICY' in riga = nota informativa
        # (deroga consapevole del comitato); sopra policy SENZA tag = violazione flaggata.
        if act in ("BUY", "ADD") and eur:
            declared = any(tag in (r.get("raw") or "").upper() for tag in POLICY_OVERRIDE_MARKERS)
            if tk in sz_pos:
                room = float(sz_pos[tk].get("remaining_capacity_eur") or 0.0)
                if eur > room + SIZING_TOLERANCE_EUR:
                    if _budget_vincola and room <= SIZING_TOLERANCE_EUR:
                        # audit 11/09: lo spazio per nome PRIMA del budget resta visibile,
                        # ma come tale — non accanto a «~0€» senza dire perche'
                        _base = text("**{} {} {:,.0f}€**: {}; policy per nome prima del budget: {}",
                                     "**{} {} {:,.0f}€**: {}; per-name policy before the budget: {}"
                                     ).format(act, tk, eur, _bud_txt, sz_pos[tk].get('verdict', ''))
                        warnings.append(
                            _base + (text(" — DEROGA DICHIARATA: valuta la motivazione del comitato",
                                          " — DECLARED OVERRIDE: assess the committee's rationale")
                                     if declared else
                                     text(" — deroga NON dichiarata (manca 'SOPRA POLICY' + motivazione in riga)",
                                          " — override NOT declared (missing '[OVER-POLICY]' and rationale in the row)")))
                    elif declared:
                        warnings.append(
                            text("**{} {} {:,.0f}€**: DEROGA DICHIARATA sopra la policy "
                                 "(spazio policy ~{:,.0f}€) — valuta la motivazione del comitato",
                                 "**{} {} {:,.0f}€**: DECLARED OVERRIDE above policy "
                                 "(policy capacity ~{:,.0f}€) — assess the committee's rationale"
                                 ).format(act, tk, eur, room))
                    else:
                        warnings.append(
                            text("**{} {} {:,.0f}€**: oltre la policy di sizing (~{:,.0f}€ — {}) e deroga NON "
                                 "dichiarata (manca 'SOPRA POLICY' + motivazione in riga)",
                                 "**{} {} {:,.0f}€**: above sizing policy (~{:,.0f}€ — {}) and override NOT "
                                 "declared (missing '[OVER-POLICY]' and rationale in the row)"
                                 ).format(act, tk, eur, room, sz_pos[tk].get('verdict', '')))
            elif _budget_vincola:
                # nome NUOVO col budget che vincola: la capacita' dispiegabile e' zero per
                # TUTTI, il 10% base per nome non e' il limite che conta (audit 11/09)
                warnings.append(
                    text("**{} {} {:,.0f}€** (nome nuovo): {}", "**{} {} {:,.0f}€** (new name): {}"
                         ).format(act, tk, eur, _bud_txt)
                    + (text(" — DEROGA DICHIARATA: valuta la motivazione del comitato",
                            " — DECLARED OVERRIDE: assess the committee's rationale") if declared
                       else text(" e deroga NON dichiarata (manca 'SOPRA POLICY' + motivazione in riga)",
                                 " and override NOT declared (missing '[OVER-POLICY]' and rationale in the row)")))
            elif invested > 0 and base_single_pct:
                max_new = invested * float(base_single_pct) / 100.0
                if eur > max_new + SIZING_TOLERANCE_EUR:
                    if declared:
                        warnings.append(
                            text("**{} {} {:,.0f}€** (nome nuovo): DEROGA DICHIARATA sopra "
                                 "la policy base (~{:,.0f}€ = {:.0f}% dell'investito) — valuta la motivazione del comitato",
                                 "**{} {} {:,.0f}€** (new name): DECLARED OVERRIDE above base policy "
                                 "(~{:,.0f}€ = {:.0f}% of invested capital) — assess the committee's rationale"
                                 ).format(act, tk, eur, max_new, base_single_pct))
                    else:
                        warnings.append(
                            text("**{} {} {:,.0f}€** (nome nuovo): sopra la policy base per "
                                 "nome (~{:,.0f}€ = {:.0f}% dell'investito, prima degli aggiustamenti "
                                 "vol/correlazione) e deroga NON dichiarata",
                                 "**{} {} {:,.0f}€** (new name): above base per-name policy "
                                 "(~{:,.0f}€ = {:.0f}% of invested capital, before volatility/correlation "
                                 "adjustments) and override NOT declared").format(act, tk, eur, max_new, base_single_pct))

        # 3b. TRIM/SELL su ticker non in book (per il sizing engine)
        if act in ("TRIM", "SELL") and sz_pos and tk not in sz_pos:
            warnings.append(text("**{} {}**: ticker non presente tra le posizioni del sizing engine",
                                 "**{} {}**: ticker is absent from the sizing engine's positions").format(act, tk))

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
                    volte = text("1 volta", "once") if n_prev == 1 else text("{} volte", "{} times").format(n_prev)
                    warnings.append(
                        text("**{} {}**: già proposta {} senza esecuzione "
                             "(prima nel memo #{} del {}, status PENDING) — "
                             "eseguirla, archiviarla o spiegare perché viene riproposta",
                             "**{} {}**: already proposed {} without execution "
                             "(first in memo #{} dated {}, status PENDING) — "
                             "execute, archive, or explain why it is being proposed again"
                             ).format(act, tk, volte, row[1], _ddmm(row[2])))
            except Exception as e:
                if "riproposte" not in controlli_db_falliti:
                    controlli_db_falliti.add("riproposte")
                    warnings.append(text("**CONTROLLO RIPROPOSTE NON ESEGUITO** — registro decisioni "
                                         "non leggibile (%s: %s)", "**REPEATED-PROPOSAL CHECK NOT PERFORMED** — "
                                         "decision register unreadable (%s: %s)") % (type(e).__name__, str(e)[:120]))

        # 5. RIPROPOSTA POST-ESECUZIONE (audit 11/09, Fable 5.1): il PM esegue un ADD e
        # una run successiva ripropone ADD sullo stesso titolo, stesso
        # verso, come se fosse nuovo. Il check 2 vede solo le PENDING. Qui: un
        # trade dello stesso verso negli ultimi 7 giorni senza un fatto NUOVO dichiarato in
        # riga (tag 'NOVITA':' nella colonna Timing, come 'SOPRA POLICY') = doppione flaggato.
        # Flag-only: nessun blocco sul titolo, la novita' dichiarata lo spegne.
        if act in ("BUY", "ADD", "TRIM", "SELL") and db is not None and tk:
            try:
                import unicodedata as _ud
                _raw_ascii = _ud.normalize("NFKD", str(r.get("raw") or "")).encode(
                    "ascii", "ignore").decode("ascii").upper()
                _novita = any(tag in _raw_ascii for tag in NEW_FACT_MARKERS)
                verso = ("BUY", "ADD") if act in ("BUY", "ADD") else ("TRIM", "SELL")
                ph = ",".join("?" * len(verso))
                cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
                with db._conn() as conn:
                    tr = conn.execute(
                        f"SELECT quantita, prezzo, valuta, data FROM trade_history "
                        f"WHERE UPPER(ticker)=? AND UPPER(action) IN ({ph}) AND data >= ? "
                        "ORDER BY data DESC", [tk, *verso, cutoff]).fetchall()
                    dec = conn.execute(
                        f"SELECT id, action FROM decisions WHERE UPPER(ticker)=? AND UPPER(action) "
                        f"IN ({ph}) AND status='EXECUTED' AND COALESCE(closed_at, timestamp) >= ? "
                        "ORDER BY id DESC LIMIT 1", [tk, *verso, cutoff]).fetchone()
                if tr and not _novita:
                    t = tr[0]
                    rif = (f"#{dec[0]} {dec[1]} {tk}" if dec else tk)
                    eur_s = f" {eur:,.0f}€" if eur else ""
                    warnings.append(
                        text("**{} {}{}**: RIPROPOSTA POST-ESECUZIONE — il PM ha gia' eseguito "
                        "{} il {} ({:g} x {:g} {}); nessuna NOVITA' "
                        "dichiarata in riga: o si dichiara il fatto nuovo con il tag 'NOVITA':' "
                        "nella colonna Timing, o la riga e' un doppione di cio' che il PM ha appena fatto",
                        "**{} {}{}**: REPROPOSED AFTER EXECUTION — the PM already executed "
                        "{} on {} ({:g} x {:g} {}); no new fact declared in the row: "
                        "declare the new fact using '[NEW-FACT]' in Timing, or this row duplicates "
                        "what the PM has just done").format(act, tk, eur_s, rif, _ddmm(t[3]), t[0], t[1], t[2] or ''))
            except Exception as e:
                if "post-esecuzione" not in controlli_db_falliti:
                    controlli_db_falliti.add("post-esecuzione")
                    warnings.append(text("**CONTROLLO RIPROPOSTE POST-ESECUZIONE NON ESEGUITO** — registro "
                                         "trade/decisioni non leggibile (%s: %s)",
                                         "**POST-EXECUTION PROPOSAL CHECK NOT PERFORMED** — trade/decision "
                                         "register unreadable (%s: %s)") % (type(e).__name__, str(e)[:120]))

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
                q = ("SELECT id, action, memo_id, pm_feedback, status FROM decisions "
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
                    # audit 11/09: lo stato (SKIPPED/ESEGUITA/...) accanto alla citazione
                    _st = {"EXECUTED": text("ESEGUITA", "EXECUTED"), "SKIPPED": "SKIPPED",
                           "EXPIRED": text("DECADUTA", "EXPIRED"), "PARTIAL": text("PARZIALE", "PARTIAL"),
                           "PENDING": text("PENDENTE", "PENDING")}
                    quotes = "; ".join(
                        "#" + str(x[0]) + " " + str(x[1]) + " (memo #" + str(x[2]) + ", "
                        + _st.get(str(x[4] or "?").upper(), str(x[4] or "?")) + "): "
                        + pm_verbatim(x[3], x[0], virgolette=True) for x in fb_rows)
                    warnings.append(
                        text("**{}**: feedback DIRETTO del PM su decisioni passate — {} — "
                        "verificare che la proposta non li contraddica o che il memo "
                        "dichiari i fatti nuovi", "**{}**: DIRECT PM feedback on past decisions — {} — "
                        "verify that the proposal does not contradict it or that the memo declares "
                        "the new facts").format(tk, quotes))
            except Exception as e:
                if "feedback" not in controlli_db_falliti:
                    controlli_db_falliti.add("feedback")
                    warnings.append(text("**CONTROLLO FEEDBACK PM NON ESEGUITO** — registro decisioni "
                                         "non leggibile (%s: %s)", "**PM FEEDBACK CHECK NOT PERFORMED** — "
                                         "decision register unreadable (%s: %s)") % (type(e).__name__, str(e)[:120]))

    if not warnings:
        return ""
    block = [text("## ACTION VALIDATOR (verifica automatica #191 — flag-only, i numeri del Capo NON sono stati modificati)",
                  "## ACTION VALIDATOR (automated check #191 — flag-only, the Capo's numbers have NOT been modified)")]
    block += [f"- {w}" for w in warnings]
    block.append(text("*(validator v1 — {}; "
                 "sizing = motore vol×corr; riproposte = decisioni PENDING nei memo "
                 "precedenti; feedback = parole del PM sulle decisioni passate, citate testualmente)*",
                 "*(validator v1 — {}; sizing = vol×corr engine; repeated proposals = PENDING decisions "
                 "in previous memos; feedback = the PM's words on past decisions, quoted verbatim)*"
                 ).format(datetime.now().strftime('%d/%m %H:%M')))
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


@scoped_language
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
        vintage = (text(" (giudizio sanity del {})", " (sanity judgement dated {})").format(judged_at)
                   if judged_at else text(" (data giudizio n.d.)", " (judgement date n.d.)"))
        if act in ("BUY", "ADD"):
            pairs.append((act, tk))
            lines.append(
                text("**{} {}**: modello corrente in **sanity BLOCK**{} → riga NON "
                "azionabile: la decisione viene auto-chiusa nel registro (SKIPPED + nota "
                "AUTO-ESCLUSA) al salvataggio. Per riproporla serve un modello rivalidato "
                "(variant view / rigenerazione)", "**{} {}**: current model in **sanity BLOCK**{} → row NOT "
                "actionable: the decision is automatically closed in the register (SKIPPED + "
                "AUTO-ESCLUSA note) when saved. Re-proposing it requires a revalidated model "
                "(variant view / regeneration)").format(act, tk, vintage))
        elif act not in ACTIONS_KNOWN:
            lines.append(
                text("**{} {}**: azione NON standard su modello in sanity BLOCK{} — "
                "il gate automatico copre solo BUY/ADD: VALUTARE A MANO (dichiarato, non muto)",
                "**{} {}**: NON-standard action on a model in sanity BLOCK{} — the automated gate "
                "only covers BUY/ADD: REVIEW MANUALLY (explicit limitation)").format(act, tk, vintage))
    if not lines:
        return "", []
    block = [text("## ACTION VALIDATOR — ESCLUSIONI HARD (sanity BLOCK, #204b)",
                  "## ACTION VALIDATOR — HARD EXCLUSIONS (sanity BLOCK, #204b)")]
    block += [f"- {ln}" for ln in lines]
    block.append(text("*(le righe restano nel memo per storia vera; l'esito dell'auto-chiusura "
                 "e' nel log della run — se una riga non viene trovata nel registro, il log lo dichiara)*",
                 "*(rows remain in the memo as historical evidence; the run log records the automatic "
                 "closure result and explicitly reports any row not found in the register)*"))
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
