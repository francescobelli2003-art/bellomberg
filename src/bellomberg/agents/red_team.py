"""
red_team.py - Agente RED TEAM / Devil's Advocate (#181 debate).

Si inserisce nel consigliere TRA il Round 1 e il Round 2: attacca i draft R1 cosi'
gli specialisti possono REPLICARE in R2 (concedere o ribattere coi dati) prima che
il Capo decida. Riceve le tesi degli specialisti e le ATTACCA sistematicamente:
assunzioni fragili, rischi sottovalutati, cosa potrebbe andare storto, dove il
consenso e' affollato. NON propone trade. Output -> blackboard.write("_red_team")
-> visibile agli specialisti (whitelist in summary_for_specialist), al Capo, e
PERSISTITO nel DB (regenerate_memo lo recupera).

Modello: Sonnet (MODEL_SYNTHESIZER) - critica, non sintesi finale: contiene i costi.
Dal 15/07 ha 3 tool READ-ONLY (portfolio_live, portfolio_risk, advanced_metrics)
e il mandato di attaccare i NUMERI: verifica le cifre che contesta [src: tool].
Robusto: se fallisce, ritorna "" e il consigliere procede senza (zero regressione).
"""

# Import al MODULO, non dentro un try/except locale (lezione 21/08: action_validator
# importava la sua policy dentro un `except Exception: pass`, e un import fallito
# avrebbe fatto sparire in silenzio TUTTI gli avvertimenti sui feedback del PM).
# Qui l'effetto e' che un import rotto fa fallire `import red_team` invece di far
# consegnare report tagliati: il difetto non puo' piu' passare per funzionamento.
# ⚠️ MA NON E' UNA MORTE RUMOROSA, e la prima stesura di questo commento lo diceva:
# il consigliere importa `run_red_team` a meta' run, DENTRO un try/except che logga
# `[!] Red team skipped: ...` e prosegue (consigliere_multi.py). In quel caso la run
# arriva al Capo SENZA contraddittorio, e ne' `consigliere_multi` ne' `capo.py` hanno
# un ramo `else` che lo dichiari nel memo: resta una riga di log. E' un buco noto,
# non curato qui — v. voce aperta in MASTER_TODO.
# Nessuna circolarita', verificato: specialists/base.py non importa red_team — la
# critica gli arriva come DATO, via blackboard.write("_red_team").
from bellomberg.agents.specialists.base import RECUPERO_NESSUNO, _blocco_blackboard, _dichiara_fallback

RED_TEAM_PROMPT = """Sei il RISK MANAGER SCETTICO di Bellomberg, l'avvocato del diavolo del team. Gli specialisti hanno prodotto le loro tesi. Il tuo compito NON e' proporre trade, ma ATTACCARE le tesi prima che il Capo decida, in italiano professionale e diretto.

Per ogni tesi o proposta rilevante degli specialisti, chiediti e scrivi:
1. QUALE ASSUNZIONE E' PIU' FRAGILE? Cosa deve essere vero perche' la tesi funzioni, e quanto e' probabile?
2. COSA PUO' ANDARE STORTO? Il rischio specifico, datato dove possibile, che farebbe fallire la tesi.
3. E' UN TRADE AFFOLLATO? Se il posizionamento e' gia' di consenso (tutti long/short la stessa cosa), dillo: il rischio e' asimmetrico al ribasso.
4. C'E' UN BIAS? Conferma di tesi precedenti, recency, ancoraggio al prezzo di carico.
   BIAS DA MOMENTUM SUI TRIM (mandato PM 16/07): attacca ANCHE le proposte di TRIM/riduzione,
   non solo i buy. Se uno specialista propone di tagliare un titolo molto sceso citando solo
   metriche TRAILING (Sharpe/maxDD/Kelly storici), chiediglielo esplicitamente: sta vendendo
   il ritardatario sul minimo? Ha valutato il lato opposto (mean-reversion, livelli, sconto
   NAV per i CEF)? Un drawdown e' sia possibile opportunita' che possibile uscita: un trim che
   considera un solo lato e' una tesi fragile quanto un buy euforico.
5. IL TIMING E' GIUSTO? O e' una buona idea al momento sbagliato (es. comprare prima di un evento binario)?
5-bis. LA TESI DEL PM E' STATA INGAGGIATA? SE ricevi il blocco TESI DEL PM per posizione: quando
   una proposta (in particolare un TRIM) contraddice una view dichiarata del PM SENZA nominarla e
   confutarla coi numeri, segnalalo come difetto di processo. NON difendere la tesi del PM a
   prescindere: se i DATI la smentiscono, di' anche questo — il PM vuole essere sfidato. Se il
   blocco manca o dichiara n.d., NON inventare tesi del PM a memoria.
6. I NUMERI TORNANO? (mandato 15/07) Attacca l'ARITMETICA, non solo le tesi. Hai 3 tool READ-ONLY
   (get_portfolio_live, get_portfolio_risk, get_advanced_metrics): usali per VERIFICARE le cifre
   chiave che gli specialisti citano, non per fare analisi tue. In particolare:
   - le % del portafoglio e il dry powder citati QUADRANO coi pesi e il cash veri del tool?
   - beta/VaR/vol citati coincidono con quelli ufficiali? Se due specialisti danno numeri
     INCOMPATIBILI tra loro (es. beta 0,04 e 0,9 nello stesso giro), dillo col numero vero accanto.
   - le somme (pesi, probabilita' di scenario) fanno ~100 o no?
   Ogni cifra che CONTESTI deve avere accanto il numero del tool [src: nome_tool]. MAI attaccare
   con numeri tuoi non verificati: un red team che allucina e' peggio di nessun red team.

REGOLE:
- Sii SPECIFICO: cita i ticker (nome esteso + ticker) e i numeri degli specialisti. Niente critiche generiche.
- Sii BREVE e tagliente: 400-700 parole totali. Il Capo deve poterlo leggere in 2 minuti.
- Concludi con "I 3 RISCHI CHE IL CAPO NON DEVE IGNORARE QUESTA SETTIMANA: ..." (lista numerata, una riga ciascuno).
- Non addolcire: il tuo valore e' dire le cose scomode che gli altri non dicono. Ma resta costruttivo, non distruttivo per partito preso."""


def _fmt_cost(v):
    """Costo in euro per la riga di log (collaudo #44). Delega a llm_pricing;
    se il modulo manca non inventa uno zero: 'n.d.' o valore grezzo."""
    try:
        from bellomberg.core.llm_pricing import format_eur
        return format_eur(v)
    except Exception:
        return "n.d." if v is None else str(v)


# §9-bis n.5: MAI una critica vuota zitta nel blackboard. Il testo e' una
# COSTANTE esportata di proposito (voce A 25/08): `capo._blocco_red_team` lo
# riconosce per instradare il segnaposto nel ramo «RED TEAM ASSENTE» invece di
# presentarlo come critica avvenuta — se cambi la frase, cambiala QUI e il
# lettore resta allineato (il test del capo scrive questo testo a registro).
SEGNAPOSTO_NESSUNA_CRITICA = ("[RED TEAM: nessuna critica prodotta (limite "
                              "iterazioni o risposta vuota) — buco dichiarato]")


def motivo_critica_non_utilizzabile(testo):
    """None se `testo` (il contenuto sotto `_red_team`) e' una critica da
    leggere; altrimenti il MOTIVO in una frase. E' l'UNICO posto dove i «vuoti
    dichiarati» del red team vengono riconosciuti (review 25/08, voci A+B):
    lo usano `capo._blocco_red_team` e il preambolo R2 dei desk — che senza
    classificazione incorniciavano un segnaposto con «un risk manager ha
    attaccato le tesi» / «A RED TEAM attacked the theses», spingendo desk e
    Capo a rispondere a obiezioni mai scritte.

    I tre «vuoti dichiarati»: il SEGNAPOSTO qui sopra; un rifiuto safeguard
    come blocco unico (llm_refusal — senza riga vuota dentro); un testo
    `[ERROR...]` (che la blackboard persiste per contratto,
    test_placeholder_non_finale). Un rifiuto A META' generazione porta testo
    pagato dopo la riga vuota ed e' una critica PARZIALE: utilizzabile."""
    t = str(testo or "").strip()
    if not t:
        return "registro `_red_team` presente ma senza testo all'ultimo round"
    if t.startswith(SEGNAPOSTO_NESSUNA_CRITICA[:40]):
        return ("il red team e' girato senza produrre critica — a registro: %s"
                % t[:200])
    if t.startswith("[ERROR"):
        return "a registro c'e' un errore, non una critica: %s" % t[:200]
    try:
        from bellomberg.core.llm_refusal import REFUSAL_TAG as _tag
    except Exception:
        _tag = "RIFIUTO DEL MODELLO"
    if t.startswith("[[") and _tag in t[:120] and "\n\n" not in t:
        return ("critica rifiutata dai safeguard del modello — a registro: %s"
                % t[:260])
    return None


def run_red_team(blackboard, portfolio_data=None, memory_db=None) -> str:
    """Esegue il red team sui report degli specialisti. Ritorna la critica (str).
    Best-effort: in caso di errore ritorna "" senza propagare."""
    import time as _time
    try:
        from bellomberg.core.llm_client import OpenRouterClient, modello as _modello_llm, somma_usage as _somma_usage
        from bellomberg.core.llm_refusal import refusal_reason as _refusal_reason
        # fix P1 14/07: importava MODEL_CONSIGLIERE (Opus) ALIASATO come synthesizer —
        # il red team girava su Opus contro il design dichiarato nel docstring.
        # 05/09 (ordine PM): il modello vive nel .env (RED_TEAM_MODEL); assente =
        # ConfigurazioneLLMMancante col nome, che finisce nel log qui sotto.
        MODEL_SYNTHESIZER = _modello_llm("red_team")
    except Exception as e:
        print(f"[RED_TEAM] import skip: {e}")
        return ""

    # raccogli i report specialisti (ultimo round di ciascuno)
    reports = {}
    try:
        for sp_name, rounds in blackboard.data.items():
            if rounds and not sp_name.startswith("_"):
                latest = max(rounds.keys())
                reports[sp_name] = {"round": latest, "report": rounds[latest]}
    except Exception as e:
        print(f"[RED_TEAM] blackboard read skip: {e}")
        return ""
    if not reports:
        return ""

    parts = ["Oggi gli specialisti hanno prodotto queste tesi. Attaccale.\n"]
    if portfolio_data:
        import json as _json
        parts.append("=== PORTAFOGLIO ATTUALE ===")
        # 20/08 (ok PM): era `[:2500]`, il taglio piu' stretto di tutti — su un dump
        # da 25.691 char il red team vedeva 2-3 posizioni su 28 e nessun totale, e
        # doveva attaccare tesi su un book che non conosceva. Stessa vista compatta
        # del Capo e degli specialisti (28/28 posizioni, totali in testa).
        try:
            from bellomberg.agents.chat_tools import _compatta_portfolio_live
            parts.append(_json.dumps(_compatta_portfolio_live(portfolio_data),
                                     default=str, ensure_ascii=False))
        except Exception as _ce:
            parts.append(_json.dumps(portfolio_data, default=str)[:2500]
                         + "\n[⚠️ VISTA COMPATTA NON DISPONIBILE (" + type(_ce).__name__
                         + "): book TRONCATO, non contiene tutte le posizioni ne' i "
                           "totali — non dedurne assenze o pesi]")
        parts.append("")
    # TESI PM (16/07): il dump del portafoglio qui sopra non porta le tesi (la vista
    # compatta le toglie apposta perche' sarebbero un duplicato);
    # il mandato 5-bis del prompt le richiede, quindi arrivano su canale dedicato.
    try:
        from bellomberg.core.current_facts import pm_theses_block
        _tesi = pm_theses_block()
        if _tesi:
            parts.append(_tesi.strip())
            parts.append("")
    except Exception as e:
        print(f"[RED_TEAM] tesi PM skip (dichiarato): {e}")
        parts.append("[CONTESTO n.d.] Tesi PM: " + type(e).__name__ + ": " + str(e))
    try:
        from bellomberg.agents.specialists import ALL_SPECIALISTS as _ALL_SP
        order = [s.name for s in _ALL_SP]  # roster vero, niente copie hardcoded (review 15/07)
    except Exception:
        order = ["macro", "fundamentals", "quant", "options", "crypto", "eventdesk"]
    order += sorted(n for n in reports if n not in order)  # difensivo: mai perdere un report
    # 21/08 (finding D di audit/25, ok PM sulla rosa (a)/(b): scelta (a) PIENO).
    # Qui c'era `txt[:5000] + "...[tronco]"`, un letterale nudo dentro il ciclo.
    # MISURATO sui report veri del memo #50: 65.340 char contro i 5.000
    # per desk che passavano, il 54,1% fuori — e cio' che cadeva era
    # sistematicamente la parte PROPOSITIVA (la "Validazione candidati" di Quant a
    # offset 5.767, che e' il gate che autorizza Options; "Le due tesi" di
    # Fundamentals a 11.388; l'income leg di Options a 6.554). Il taglio ERA
    # dichiarato — "[tronco]" — ma senza numeri: diceva CHE, mai QUANTO.
    # ⚠️ Cio' che NON si puo' dire (correzione del confutatore, 21/08): che il red
    # team "lasciasse passare le proposte senza esame". La put spread SPY 735/700
    # di Options stava DENTRO il cap ed e' stata attaccata nel merito in produzione.
    # Ora si riusa la funzione dei desk: stessa finestra (MAX_CHAR_BLACKBOARD, un
    # numero solo), riparto equo se mai mordesse, dichiarazione per desk coi numeri.
    # `recupero=RECUPERO_NESSUNO` perche' il red team ha 3 soli tool READ-ONLY
    # (RED_TEAM_TOOLS, sotto) e NON ha ask_specialist: la via di recupero dei desk
    # per lui non esiste, e prometterla sarebbe la stessa bugia del 21/08 ripetuta.
    ordinati = {sp: reports[sp] for sp in order if sp in reports}
    # 22/08 sera-2 (voce (2b), ok PM): il red team attaccava tesi senza aver
    # mai visto i feedback e i veti del PM — poteva chiedere a un desk di
    # «considerare» un'idea che il PM aveva gia' vietato. Stesso blocco di
    # R1/R2, PRIMA dei report; tre stati dichiarati (v. specialists.base).
    try:
        from bellomberg.agents.specialists.base import blocco_vincoli_pm as _blocco_vincoli_pm
        _mdb = memory_db if memory_db is not None else getattr(blackboard, "memory_db", None)
        _v = _blocco_vincoli_pm(_mdb)
        if _v:
            parts.append(_v.rstrip("\n"))
            parts.append("")
        # 27/08 (run V9): lo stato del blocco a log (punto 3 della checklist).
        # Prima nessuna traccia: `blocco_vincoli_pm` non solleva mai (rende il
        # blocco NON DISPONIBILI), quindi l'except qui sotto scatta solo su
        # import falliti - nemmeno il registro guasto lasciava una riga.
        from bellomberg.agents.specialists.base import riga_log_vincoli_pm as _riga_log_vincoli_pm
        print("[RED_TEAM] vincoli PM: " + _riga_log_vincoli_pm(_v))
    except Exception as e:
        print(f"[RED_TEAM] vincoli PM skip (dichiarato): {e}")
    parts.append(_blocco_blackboard(ordinati, recupero=RECUPERO_NESSUNO))
    from bellomberg.valuation.sector_analysis import valuation_results_block
    parts.append(valuation_results_block(getattr(blackboard, "valuation_results", {})))
    user_msg = "\n".join(parts)

    # mandato sui numeri (15/07): 3 tool READ-ONLY dal registro delle chat.
    # Best-effort dichiarato: il modello deve sapere quali verifiche non puo' fare.
    RED_TEAM_TOOLS = ("get_portfolio_live", "get_portfolio_risk", "get_advanced_metrics")
    tools_schema = []
    registry_error = None
    try:
        from bellomberg.agents import chat_tools
        tools_schema = [t for t in chat_tools.TOOL_DEFINITIONS if t["name"] in RED_TEAM_TOOLS]
    except Exception as e:
        registry_error = e
    presenti = {t["name"] for t in tools_schema}
    mancanti = [nome for nome in RED_TEAM_TOOLS if nome not in presenti]
    if mancanti:
        avviso = _dichiara_fallback(
            "run_red_team.tools_schema",
            registry_error if registry_error is not None else LookupError("schemi assenti dal registro"),
            "Tool non disponibili: " + ", ".join(mancanti))
        user_msg += ("\n\n[!! ARSENALE DEGRADATO - DICHIARALO NEL REPORT]\n" + avviso
                     + "\nDichiara quali numeri non hai potuto verificare con questi tool; "
                       "non colmare i dati mancanti a memoria.")

    print(f"[RED_TEAM] context {len(user_msg)} chars, model {MODEL_SYNTHESIZER}, "
          f"{len(tools_schema)} tool read-only")
    # collaudo #44: usage/durata/chiamate dichiarati FUORI dal try — se l'API muore
    # a meta' tool-loop i token gia' spesi devono comunque finire nel conto della run
    _usage = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0}
    # True se ALMENO una delle 4 iterazioni del tool-loop non ha esposto usage: il
    # totale dell'agente e' allora una sottostima di entita' ignota -> status
    # "usage_unknown", mai "ok" (semantica dei buchi, PM 15/07).
    _usage_unknown = False
    _calls = 0
    _t0 = _time.perf_counter()
    try:
        client = OpenRouterClient(timeout=240.0, max_retries=1)
        messages = [{"role": "user", "content": user_msg}]
        critique = ""
        # mini tool-loop (max 3 giri di verifica + risposta finale): il red team
        # VERIFICA i numeri che attacca invece di fidarsi o inventare
        _forced_final = False
        for _it in range(4):
            _calls += 1
            _kw = {"tools": tools_schema} if tools_schema else {}
            # §9-bis n.5 (ok PM 21/07): l'ULTIMO giro e' SEMPRE la critica — tool_choice
            # none + nudge. Prima, 4 giri tutti tool_use = critique "" scritta VUOTA e
            # zitta nel blackboard (memo senza red team, nessuna dichiarazione).
            if _it == 3 and tools_schema:
                _forced_final = True
                _kw["tool_choice"] = {"type": "none"}
                _lastm = messages[-1]
                _ndg = ("LIMITE VERIFICHE RAGGIUNTO: tool disabilitati. Scrivi ORA la "
                        "critica finale coi dati che hai; dichiara cio' che non hai "
                        "potuto verificare, senza inventare numeri.")
                if isinstance(_lastm.get("content"), list):
                    _lastm["content"].append({"type": "text", "text": _ndg})
                else:
                    _lastm["content"] = str(_lastm.get("content") or "") + "\n\n" + _ndg
            resp = client.messages.create(
                model=MODEL_SYNTHESIZER,
                # E 16/07: era 2000 e la critica della run #45 e' stata TRONCATA a meta'
                # frase (5.917 char, stop su max_tokens): mancavano i "3 RISCHI" finali.
                # 26/07 pre-V6: 3000 -> 4200. MISURATO con count_tokens su QUESTO
                # prompt: RED_TEAM_PROMPT = 1098 token su sonnet-4-6 e 1505 su
                # sonnet-5, cioe' +37,1% (la doc dice ~+30%: sull'italiano e' peggio).
                # Quindi i 3000 tarati su 4.6 valgono ~2.190 token vecchi: appena
                # sopra i 2000 che il 16/07 avevano GIA' troncato la critica. 4200
                # ripristina il margine reale di allora. Il tetto non e' una spesa
                # (l'output si paga a consumo): previene il troncamento, non lo compra.
                max_tokens=4200,
                # 05/09 (ordine PM «accendiamo anche il ragionamento»): ACCESO (effort medium
                # via llm_client). Prima (26/07) era SPENTO esplicito per il budget: i token
                # di ragionamento contano nei 4200 e in usage.reasoning_tokens — se la critica
                # esce troncata, il WARN qui sotto lo dice e il tetto si alza.
                thinking={"type": "adaptive"},
                system=RED_TEAM_PROMPT,
                messages=messages,
                **_kw,
            )
            # Se la risposta non espone usage i token NON diventano zero in silenzio:
            # zero direbbe "questo giro non e' costato nulla" su una chiamata vera.
            # Il buco si segna e a fine agente lo status diventa "usage_unknown"
            # (token IGNOTI) invece di "ok".
            try:
                _usage = _somma_usage(_usage, getattr(resp, "usage", None))
                _usage_unknown = _usage["tokens_status"] == "parziale"
            except Exception as e:
                _usage_unknown = True
                print(f"[RED_TEAM] WARN usage non esposto dalla risposta "
                      f"(iter {_it + 1}): {e}")
            if resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": resp.content})
                results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        print(f"[RED_TEAM] -> {block.name}({str(block.input)[:60]})")
                        try:
                            from bellomberg.agents import chat_tools; import json as _j
                            # V6 Lotto 3 (review B4): attribuzione del chiamante
                            r = chat_tools.dispatch(block.name, block.input or {},
                                                    caller="red-team")
                            r_str = _j.dumps(r, default=str, ensure_ascii=False)
                            # Voce 11 §9-quattuortrigies (01/08): unico dei tre punti
                            # di taglio che troncava MUTO — ora stesso idioma
                            # dichiarato di specialists/base.py:949.
                            # 20/08 (ok PM): tetto letto da chat_tools, unica fonte di
                            # verita' — v. specialists/base.py e tests/test_tetto_tool_result.py
                            try:
                                from bellomberg.agents.chat_tools import TETTO_TOOL_RESULT as _TETTO
                            except Exception:
                                _TETTO = 6000
                            if len(r_str) > _TETTO:
                                r_str = r_str[:_TETTO] + "...[truncated]"
                        except Exception as te:
                            r_str = f"[tool error dichiarato: {te}]"
                        results.append({"type": "tool_result",
                                        "tool_use_id": block.id, "content": r_str})
                messages.append({"role": "user", "content": results})
                continue
            # audit/11 §5: concatena TUTTI i blocchi text (stessa classe di bug di
            # specialists/base: la coda 'I 3 RISCHI...' e' la prima a perdersi)
            _parts = [b.text for b in resp.content if hasattr(b, "text")]
            critique = "\n".join(p for p in _parts if p)
            # E 16/07: se il modello ha sbattuto sul tetto token il testo finisce a meta'
            # frase — il buco si DICHIARA nel testo stesso (il Capo e il DB lo vedono),
            # non si lascia una critica che sembra completa.
            if getattr(resp, "stop_reason", None) == "max_tokens":
                critique += "\n[CRITICA TRONCATA: raggiunto il limite di token del red team]"
                print("[RED_TEAM] WARN: critica troncata su max_tokens (dichiarato nel testo)")
            # 26/07 pre-V6: il rifiuto dei safeguard non solleva (HTTP 200) e qui
            # produceva una critica vuota che finiva nel paracadute generico piu'
            # sotto, senza causa. Con Sonnet 5 la fascia e' piu' sensibile: si dichiara.
            _rif = _refusal_reason(resp, "RED_TEAM")
            if _rif:
                print("[RED_TEAM] " + _rif[:220])
                critique = (_rif + "\n\n" + critique) if critique.strip() else _rif
            # §9-bis n.5: la critica del giro forzato si dichiara in testa
            if critique and _forced_final:
                critique = ("[CRITICA AL LIMITE VERIFICHE (4 giri): tool esauriti, "
                            "buchi dichiarati nel testo]\n" + critique)
            break
        # §9-bis n.5: MAI una critica vuota zitta nel blackboard (fallback 14/07) —
        # possibile ormai solo se anche il giro forzato non produce testo
        if not critique:
            critique = SEGNAPOSTO_NESSUNA_CRITICA
            print("[RED_TEAM] WARN: critica vuota, scritto il buco dichiarato")
        # P1 14/07: via Blackboard.write (round 1 = post-R1) invece dell'assegnazione
        # diretta: cosi' la critica PERSISTE nel DB (gate dedicato in base.write) e
        # regenerate_memo non rigenera piu' il memo SENZA red team.
        try:
            blackboard.write("_red_team", 1, critique)
        except Exception:
            pass
        # collaudo #44: il red team entra nel conto della run come gli specialisti.
        # cache_ttl=None: NON usa prompt caching (nessun cache_control nelle sue
        # chiamate) -> cache_read/cache_write restano 0, ed e' corretto cosi'.
        # Resta best-effort: se la contabilita' esplode, la run NON muore.
        _entry = None
        try:
            _entry = blackboard.record_usage(
                "_red_team", 1, MODEL_SYNTHESIZER, _usage,
                duration_s=round(_time.perf_counter() - _t0, 2),
                api_calls=_calls, cache_ttl=None,
                status="usage_unknown" if _usage_unknown else "ok")
        except Exception as ue:
            print(f"[RED_TEAM] usage non registrato (procedo): {ue}")
        print(f"[RED_TEAM] critica generata: {len(critique)} chars")
        _cost = (_entry or {}).get("cost_eur")
        print(f"[RED_TEAM] usage: in={_usage['in'] if _usage['in'] is not None else 'n.d.'} out={_usage['out'] if _usage['out'] is not None else 'n.d.'} "
              f"cache_read={_usage['cache_read'] if _usage['cache_read'] is not None else 'n.d.'} cache_write={_usage['cache_write'] if _usage['cache_write'] is not None else 'n.d.'} "
              f"({_calls} call API, costo {_fmt_cost(_cost)})")
        return critique
    except Exception as e:
        print(f"[RED_TEAM] API error (procedo senza): {e}")
        # anche qui i token gia' spesi vanno dichiarati, con lo status dell'errore
        # (regola no-fallback-silenziosi PM 14/07): un agente fallito non sparisce
        # dai costi. Nested try: il red team non deve MAI far cadere la run.
        try:
            blackboard.record_usage("_red_team", 1, MODEL_SYNTHESIZER, _usage,
                                    duration_s=round(_time.perf_counter() - _t0, 2),
                                    api_calls=_calls, cache_ttl=None, status="api_error")
        except Exception as ue:
            print(f"[RED_TEAM] usage non registrato (procedo): {ue}")
        return ""
