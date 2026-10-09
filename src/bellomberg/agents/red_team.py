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
from bellomberg.core.language import prompt_for_language, scoped_language
from datetime import datetime, timezone
from copy import deepcopy

from bellomberg.core.trade_idea_policy import role_thinking
from bellomberg.market_data.freschezza_trimestrale import as_of_freschezza as _as_of_freschezza  # cutoff della run (R-CASCATA 07/10)

TRADE_IDEA_RED_MAX_TOKENS = 128000
WEEKLY_RED_MAX_TOKENS = 128000

EVIDENCE_FOLLOWUP_INSTRUCTIONS = (
    "\nCONTRADDITTORIO SULLE RICEVUTE: ricevi fatti e lacune della run, non solo tesi "
    "degli altri desk. Verifica periodo/FY/durata, identita' dell'emittente, valuta e "
    "quotazione dalla ricevuta propria; chiedi evidenza mancante nella critica senza "
    "nuove chiamate automatiche ai provider. Confermare un dato senza fonte resta "
    "UNVERIFIED, non PASS. Contraddizione solo per valori o metadati esplicitamente "
    "incompatibili nella medesima fonte e perimetro; dati corretti non meritano allarmi. "
    "Dopo la prosa, aggiungi un solo blocco ```evidence_review con JSON {\"claims\": [...]} "
    "e chiudi con ```. Per ciascun fatto discusso copia source_receipt integralmente "
    "(sha256/tool/path/index e metadata_paths quando presente) "
    "dalla ricevuta fornita, metric, value, ticker, issuer_name, period_start, period_end, "
    "duration, fiscal_year_label, unit, currency, definition, observed_at. Riporta le "
    "tue affermazioni esplicite in questi campi; null quando non attestati, mai inventare "
    "metadati. Puoi aggiungere method e perimeter solo se espliciti. Non ricopiare tutti "
    "i dati: confronta quelli discussi. Nessun claim confrontabile = claims vuoto. "
    "Il confronto locale riguarda solo questi campi: prosa libera NOT_ASSESSED, "
    "coincidenza numerica NON prova verita' economica o correttezza del metodo.\n"
)


def _review_evidence_claims(critique, evidence):
    """Compare a closed declaration grammar with the frozen receipt projection only."""
    import json
    import math
    import re
    from hashlib import sha256
    from bellomberg.agents.specialists.base import _checkpoint_digest
    from bellomberg.core.evidence_followup_policy import POLICY
    report = {"policy": POLICY, "response_sha256": sha256(critique.encode()).hexdigest(),
              "evidence_sha256": _checkpoint_digest(evidence), "status": "NOT_ASSESSED",
              "semantic_scope": "NOT_ASSESSED", "claims": [], "issues": [],
              "limitations": ["EXPLICIT_FIELDS_ONLY", "NUMERIC_EQUALITY_NOT_ECONOMIC_TRUTH",
                              "PROSE_AND_OMITTED_CLAIMS_NOT_ASSESSED"]}
    blocks = re.findall(r'^```evidence_review[^\S\n]*\n(.*?)\n```[^\S\n]*$',
                        critique.replace('\r\n', '\n'), re.M | re.S)
    if not blocks and 'evidence_review' not in critique:
        report["issues"].append("NO_STRUCTURED_CLAIMS")
        return report
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = value
        return out
    def nonfinite(value):
        raise ValueError("nonfinite value")
    try:
        if len(blocks) != 1:
            raise ValueError("one block required")
        parsed = json.loads(blocks[0], object_pairs_hook=unique, parse_constant=nonfinite)
        if not isinstance(parsed, dict) or set(parsed) != {"claims"} or not isinstance(parsed["claims"], list):
            raise ValueError("unsupported review")
    except (ValueError, TypeError):
        report.update(status="UNVERIFIED", issues=["REVIEW_FORMAT_UNVERIFIED"])
        return report
    required = {"source_receipt", "metric", "value", "ticker", "issuer_name", "period_start",
                "period_end", "duration", "fiscal_year_label", "unit", "currency", "definition", "observed_at"}
    allowed = required | {"method", "perimeter"}
    for index, claim in enumerate(parsed["claims"]):
        finding = {"index": index, "status": "UNVERIFIED", "reasons": []}
        report["claims"].append(finding)
        if not isinstance(claim, dict) or set(claim) - allowed:
            finding["reasons"].append("UNSUPPORTED_CLAIM")
            continue
        ref = claim.get("source_receipt")
        reference_keys = {'sha256', 'tool', 'path', 'index'}
        if (not isinstance(ref, dict) or set(ref) not in (reference_keys, reference_keys | {'metadata_paths'})
                or type(ref.get('index')) is not int or ref['index'] < 0
                or not isinstance(ref.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', ref['sha256'])
                or not isinstance(ref.get('path'), str) or not ref['path'].startswith('/')
                or not isinstance(ref.get('tool'), str) or not ref['tool']):
            finding['reasons'].append('SOURCE_RECEIPT_UNVERIFIED')
            continue
        if 'metadata_paths' in ref:
            paths = ref['metadata_paths']
            if (not isinstance(paths, list) or not paths
                    or any(not isinstance(path, str) or (path != '' and not path.startswith('/'))
                           or re.search(r'~(?![01])', path) for path in paths)
                    or len(paths) != len(set(paths))):
                finding['reasons'].append('SOURCE_RECEIPT_UNVERIFIED')
                continue
        matches = [fact for fact in evidence.get("facts", [])
                   if isinstance(ref, dict) and fact.get("source_receipt") == ref]
        if len(matches) != 1:
            finding["reasons"].append("SOURCE_RECEIPT_UNVERIFIED")
            continue
        fact = matches[0]
        contradicted, missing = [], []
        for field in sorted((required | (set(claim) & allowed)) - {"source_receipt"}):
            actual, stated = fact.get(field), claim.get(field)
            if (actual is None or actual == '' or stated is None or stated == ''
                    or type(stated) not in (str, int, float) or type(actual) not in (str, int, float)
                    or isinstance(stated, float) and not math.isfinite(stated)
                    or isinstance(actual, float) and not math.isfinite(actual)):
                missing.append(field)
            elif field == 'value' and (type(stated) not in (int, float) or fact.get('value_status') != 'AVAILABLE'):
                missing.append(field)
            elif actual != stated:
                contradicted.append(field)
        if fact.get('identity_basis') in (None, 'UNVERIFIED'):
            missing.append('identity_basis')
        finding['reasons'] = ['MISSING_OR_UNSUPPORTED:' + field for field in missing]
        if contradicted:
            finding.update(status='CONTRADICTION_EXPLICIT', contradicted_fields=contradicted)
        elif not missing:
            finding['status'] = 'CONSISTENT_EXPLICIT'
    statuses = {row['status'] for row in report['claims']}
    report['status'] = ('CONTRADICTION_EXPLICIT' if 'CONTRADICTION_EXPLICIT' in statuses else
                        'UNVERIFIED' if 'UNVERIFIED' in statuses else
                        'ASSESSED_EXPLICIT' if statuses else 'NOT_ASSESSED')
    return report


# MOD-CAP (06/10, Opus 5.5): il tetto di uscita si adatta al provider nel punto UNICO del
# client (llm_client.tetto_uscita, prima del preventivo del registro e dell'invio). Qui non
# c'e' piu' un secondo meccanismo: il contratto del checkpoint resta sul cap RICHIESTO
# (WEEKLY_RED_MAX_TOKENS) e il corpo sul filo porta il cap adattato. Garanzie che prima
# viveva qui (_cap_del_provider, _red_team_mai_inviato misurato sul registro, V0-REDTEAM):
#   - mai inviato (nessuna riga nel registro) -> parte col cap adattato;
#   - gia' inviato/pagato col cap richiesto -> il registro lo rigioca o lo blocca col SUO
#     corpo (RequestJournal.prepare, forme_precedenti): nessuna doppia spesa.
# Un checkpoint salvato dal codice 05-06/10 col cap gia' tagliato (contratto al tetto) resta
# riprendibile: e' lo stesso corpo che il punto centrale manda oggi.


class RedTeamSenzaPreventivo(RuntimeError):
    """Preventivo del Red Team fallito PRIMA di qualsiasi invio (listino Models API non
    leggibile o cap rifiutato dal controllo prezzi): nessuna spesa per costruzione. Decisione
    PM/main 05/10: e' una lacuna DICHIARATA (weekly_lifecycle._red_team_local_failure, per tipo),
    la run prosegue senza contraddittorio; non e' un errore di run."""


def _senza_preventivo(model, max_tokens, dettaglio, exc):
    return RedTeamSenzaPreventivo("Red Team senza preventivo prima dell'invio (nessuna spesa), modello "
                                  + str(model) + ", max_tokens " + str(max_tokens) + ": " + dettaglio)


def _valida_listino(meta):
    """Forma del listino della Models API (dato del FORNITORE): ogni anomalia e' ValueError, cosi'
    diventa lacuna dichiarata senza allargare l'except a TypeError/AttributeError, che restano
    errori di programmazione (R-0RT F-C, 06/10)."""
    from decimal import Decimal, InvalidOperation
    if not isinstance(meta, dict):
        raise ValueError("listino del modello non e' un oggetto (" + type(meta).__name__ + ")")
    if type(meta.get("context_length")) is not int:
        raise ValueError("listino senza context_length intero")
    top = meta.get("top_provider")
    if top is not None and not isinstance(top, dict):
        raise ValueError("listino con top_provider non valido (" + type(top).__name__ + ")")
    pricing = meta.get("pricing")
    if not isinstance(pricing, dict):
        raise ValueError("listino senza pricing (" + type(pricing).__name__ + ")")
    for chiave in ("prompt", "completion"):
        grezzo = pricing.get(chiave)
        if isinstance(grezzo, bool) or not isinstance(grezzo, (str, int, float)):
            raise ValueError("listino: pricing " + chiave + " assente o non numerico")
        try:
            valore = Decimal(str(grezzo))
        except InvalidOperation:
            raise ValueError("listino: pricing " + chiave + " non numerico") from None
        if not valore.is_finite() or valore < 0:
            raise ValueError("listino: pricing " + chiave + " non finito o negativo")


def _preventivo_prima_dell_invio(model, max_tokens):
    """Lo stesso controllo che il registro richieste fa a ogni invio (listino memorizzato +
    preparation_price_ceiling), anticipato: cosi' il suo rifiuto e' riconoscibile come
    'nessun invio fatto'. Restituisce il max_tokens che partira' sul filo (il richiesto adattato
    al tetto del provider dal punto centrale, MOD-CAP): il contratto del chiamante non cambia.
    Revisione R-0RT (06/10):
    - F-B: max_tokens non intero positivo = errore di PROGRAMMAZIONE (TypeError), prima del try;
      il ValueError diventa lacuna solo dalle due fonti legittime: lettura del catalogo (anche
      «modello configurato assente», scelta main) e rifiuto del preventivo.
    - F-C: listino anomalo validato (_valida_listino) -> ValueError -> lacuna.
    - F-D: il tetto viene dal listino letto QUI (lo stesso del registro), non da una lettura
      precedente fallita.
    Della rete solo il tipo (il testo puo' contenere URL). Senza registro non c'e' controllo
    prezzi a valle: niente da anticipare."""
    if type(max_tokens) is not int or max_tokens <= 0:
        raise TypeError("Red Team: max_tokens deve essere un intero positivo, non " + repr(max_tokens))
    from bellomberg.core.request_journal import current_request_scope
    registro = (current_request_scope() or {}).get("journal")
    if registro is None:
        return max_tokens
    from bellomberg.core.llm_pricing import preparation_price_ceiling
    try:
        meta, _gia_letto = registro.metadati_modello(model)
    except OSError as exc:
        raise _senza_preventivo(model, max_tokens, type(exc).__name__ + " (catalogo modelli non letto)", exc) from exc
    except ValueError as exc:
        raise _senza_preventivo(model, max_tokens, "catalogo modelli: " + type(exc).__name__ + ": "
                                + str(exc)[:200], exc) from exc
    try:
        _valida_listino(meta)
    except ValueError as exc:
        raise _senza_preventivo(model, max_tokens, str(exc)[:200], exc) from exc
    from bellomberg.core.llm_client import tetto_uscita
    # R-MOD F1: lo stesso tetto CONGELATO per la run che usa il client (stesso corpo sul filo).
    tetto, _congelato = registro.tetto_congelato(model)
    if type(tetto) is int and tetto > 0:
        max_tokens = tetto_uscita(model, max_tokens, ruolo="red_team", tetto_provider=tetto)
    try:
        preparation_price_ceiling(meta, model=model, max_tokens=max_tokens)
    except ValueError as exc:
        raise _senza_preventivo(model, max_tokens, "preventivo rifiutato: " + str(exc)[:200], exc) from exc
    return max_tokens

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
# audit 11/09: prefisso del guasto API dichiarato nel registro (v. except di run_red_team)
SEGNAPOSTO_NON_DISPONIBILE = "[RED TEAM NON DISPONIBILE:"


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
    if t.startswith(SEGNAPOSTO_NON_DISPONIBILE):
        # audit 11/09: guasto API dichiarato (es. HTTP 403 sul modello): la causa vera
        # arriva al Capo e ai desk R2, non «non risulta girato»
        return "il red team NON e' disponibile in questa run — a registro: %s" % t[:300]
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



def _validate_citation_correction(original, corrected, catalog):
    """The native provider may change exact evidence IDs only, never the analysis."""
    import json
    from copy import deepcopy
    from bellomberg.core.trade_idea_contract import validate_committee_review
    from bellomberg.agents.trade_idea import _plan_digest
    before, after = json.loads(original), json.loads(corrected)
    validate_committee_review(before)
    validate_committee_review(after)
    allowed = {row["id"] for row in catalog}
    for row in after["objections"]:
        if set(row["evidence_refs"]) - allowed:
            raise ValueError("Citation correction contains an unknown exact evidence ID")
    def analysis(value):
        value = deepcopy(value)
        for row in value["objections"]:
            row.pop("evidence_refs")
        return value
    if _plan_digest(analysis(before)) != _plan_digest(analysis(after)):
        raise ValueError("Citation correction changed the immutable analysis")
    return after


def _run_citation_correction(blackboard, original):
    """One explicitly requested native correction; the full/weekly Red loop is unchanged."""
    import json
    import math
    import time
    from bellomberg.core.llm_client import OpenRouterClient, somma_usage
    from bellomberg.agents.specialists.base import timeout_specialisti
    from bellomberg.core.trade_idea_contract import validate_committee_review, TRADE_IDEA_REVIEW_SCHEMA
    from bellomberg.agents.trade_idea import candidate_model_context, model_for_role, _plan_digest
    parsed = validate_committee_review(original)
    context = candidate_model_context(blackboard, purpose="committee")
    catalog = context["review_evidence_catalog"]
    model = model_for_role("red_team", blackboard)  # MOD-TI 06/10: dal contratto della run
    gate = getattr(blackboard, "budget_gate", None)
    if gate is None:
        raise ValueError("Trade Idea citation correction requires its native budget gate")
    blackboard.data.pop("_red_team_citation_correction", None)
    usage = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0}
    started, calls, status = time.perf_counter(), 0, "api_error"
    try:
        client = gate.wrap_client(OpenRouterClient(
            timeout=timeout_specialisti(TRADE_IDEA_RED_MAX_TOKENS), max_retries=0), role="red_team")
        calls = 1
        response = client.messages.create(model=model, max_tokens=TRADE_IDEA_RED_MAX_TOKENS,
            thinking=role_thinking(blackboard, 'red_team'),
            system=prompt_for_language("Correct only evidence_refs in the supplied immutable CommitteeReview. "
                "Return the complete JSON with every other field/value and list order unchanged. "
                "Use exact catalog IDs only, preserving admitted_document/retrieved_tool/derived_model/desk_opinion kinds. "
                "Do not conduct new research or change any question, objection, desk, category, material flag or requested_change. "
                "A descriptive citation is not an ID. If no supplied ID supports it, use an empty evidence_refs list. "
                "Never invent a source or promote software calculations/peer opinions to primary financial facts."),
            messages=[{"role": "user", "content": json.dumps({"mode": "citation_correction_only",
                "original_review": parsed, "original_review_sha256": _plan_digest(original),
                "review_evidence_catalog": catalog, "evidence_refs_contract": context["evidence_refs_contract"]},
                ensure_ascii=False, allow_nan=False)}],
            response_format={"type": "json_schema", "json_schema": {
                "name": "trade_idea_committee_review", "strict": True, "schema": TRADE_IDEA_REVIEW_SCHEMA}})
        usage = somma_usage(usage, getattr(response, "usage", None))
        text = "\n".join(block.text for block in response.content if getattr(block, "type", None) == "text" and block.text)
        blackboard.data["_red_team_native_terminal"] = {"response_id": getattr(response, "id", None),
            "stop_reason": getattr(response, "stop_reason", None), "text_present": bool(text.strip()), "text_chars": len(text)}
        cost = usage.get("cost_usd")
        if (getattr(response, "stop_reason", None) != "end_turn" or not text.strip()
                or usage.get("tokens_status") != "completo" or isinstance(cost, bool)
                or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0):
            raise ValueError("Native citation correction is incomplete or its usage is unknown")
        _validate_citation_correction(original, text, catalog)
        blackboard.write("_red_team", 1, text)
        blackboard.data["_red_team_citation_correction"] = {"original_report_sha256": _plan_digest(original),
            "corrected_report_sha256": _plan_digest(text), "catalog_sha256": _plan_digest(catalog),
            "response_id": getattr(response, "id", None), "evidence_refs_only": True}
        status = "ok"
        return text
    except Exception as exc:
        blackboard.write("_red_team", 1, SEGNAPOSTO_NON_DISPONIBILE + " citation correction rejected: " + str(exc)[:300] + "]")
        return ""
    finally:
        blackboard.record_usage("_red_team", 1, model, usage,
            duration_s=round(time.perf_counter() - started, 2), api_calls=calls, cache_ttl=None, status=status)


@scoped_language
def run_red_team(blackboard, portfolio_data=None, memory_db=None, *, citation_correction=None) -> str:
    """Esegue il red team sui report degli specialisti. Ritorna la critica (str).
    Best-effort: in caso di errore ritorna "" senza propagare."""
    import time as _time
    trade_idea = getattr(blackboard, "run_scope", "weekly") == "trade_idea"
    from bellomberg.core.evidence_prompt_policy import enabled as evidence_prompts_enabled
    evidence_prompts_enabled(blackboard)  # Validate before any best-effort import/config path.
    from bellomberg.core.evidence_followup_policy import board_enabled
    board_enabled(blackboard)
    if citation_correction is not None:
        if not trade_idea:
            raise ValueError("Citation correction belongs only to Trade Idea")
        return _run_citation_correction(blackboard, citation_correction)
    try:
        from bellomberg.core.llm_client import OpenRouterClient, modello as _modello_llm, somma_usage as _somma_usage
        from bellomberg.core.llm_refusal import refusal_reason as _refusal_reason
        # fix P1 14/07: importava MODEL_CONSIGLIERE (Opus) ALIASATO come synthesizer —
        # il red team girava su Opus contro il design dichiarato nel docstring.
        # 05/09 (ordine PM): il modello vive nel .env (RED_TEAM_MODEL); assente =
        # ConfigurazioneLLMMancante col nome, che finisce nel log qui sotto.
        if trade_idea:
            from bellomberg.agents.trade_idea import model_for_role
            MODEL_SYNTHESIZER = model_for_role("red_team", blackboard)  # contratto della run, mai il .env di oggi
        else:
            MODEL_SYNTHESIZER = _modello_llm("red_team")
    except Exception as e:
        print(f"[RED_TEAM] import skip: {e}")
        callback = getattr(blackboard, "record_run_failure", None)
        if callable(callback):
            callback(e, desk="red_team", round_n=1)
            raise
        return ""

    from bellomberg.agents.specialists.base import _checkpoint_digest
    checkpoint_key = None
    saved_checkpoint = None
    if callable(getattr(blackboard, "persist_run_checkpoint", None)):
        checkpoint_key = "red_team:R1"
        if trade_idea:
            current_model = blackboard.valuation_results.get(blackboard.target_ticker) or {}
            reference = {key: current_model.get(key) for key in
                         ("snapshot_id", "generation_id", "workbook_sha256")}
            checkpoint_key += ":model:" + _checkpoint_digest(reference)
        saved_checkpoint = deepcopy(getattr(blackboard, "specialist_checkpoints", {}).get(checkpoint_key))
        if saved_checkpoint is not None:
            stored_digest = saved_checkpoint.pop("sha256", None)
            if stored_digest != _checkpoint_digest(saved_checkpoint):
                raise ValueError("Red Team checkpoint checksum differs")
            # No refreshed PM memory or live book is mixed into a received request.
            return _run_red_team_loop(blackboard, trade_idea, MODEL_SYNTHESIZER,
                saved_checkpoint["user_msg"], saved_checkpoint["tools_schema"],
                checkpoint_key, saved_checkpoint)

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
    if trade_idea:
        import json as _json
        parts.append("CANDIDATO UNICO: " + str(blackboard.target_ticker))
        parts.append("VIEW PM DA ATTACCARE COME TESI, NON COME FONTE: "
                     + (blackboard.pm_view or "(assente)"))
        parts.append("DECISIONI, VETI, NOTE PM E TRADE RECENTI DEL TICKER: "
                     + _json.dumps(blackboard.data.get("_decision_context") or
                                   {"status": "unavailable"}, ensure_ascii=False, default=str))
    else:
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
        if trade_idea:
            # E7 (04/10/2026, Opus 5.5): la Trade Idea non consegna il DB (memory_db=None per
            # disegno): il blocco arriva dalla fotografia in sola lettura della run, e un buco
            # e' una frase NON DISPONIBILI, mai la stringa vuota.
            from bellomberg.agents.trade_idea import pm_constraints_text
            _v = pm_constraints_text(blackboard)[0]
        else:
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
    from bellomberg.core.research_analysis import is_research_mode, research_context
    if not trade_idea and is_research_mode(blackboard):
        parts.append('SEALED COMPANY RESEARCH AND R1 THESIS:\n' + _json.dumps(
            research_context(blackboard), ensure_ascii=False, default=str))
    else:
        from bellomberg.valuation.sector_analysis import valuation_results_block
        parts.append(valuation_results_block(getattr(blackboard, "valuation_results", {})))
    if trade_idea:
        from bellomberg.agents.trade_idea import candidate_model_context
        parts.append("EXACT COMMON MODEL DRIVERS AND EVIDENCE:\n" + _json.dumps(
            candidate_model_context(blackboard, purpose="committee"), ensure_ascii=False, default=str))
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

    return _run_red_team_loop(blackboard, trade_idea, MODEL_SYNTHESIZER, user_msg,
                              tools_schema, checkpoint_key, saved_checkpoint)


def _run_red_team_loop(blackboard, trade_idea, MODEL_SYNTHESIZER, user_msg,
                      tools_schema, checkpoint_key, saved_checkpoint):
    """Resume exact paid messages; a completed or ambiguous tool is never repeated."""
    import time as _time
    from bellomberg.core.llm_client import OpenRouterClient, somma_usage as _somma_usage, thinking_fase
    from bellomberg.core.llm_refusal import refusal_reason as _refusal_reason
    from bellomberg.core.research_analysis import is_research_mode
    from bellomberg.agents.specialists.base import (
        _checkpoint_json, _checkpoint_digest, _persist_specialist_checkpoint, timeout_specialisti)
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
    _request_inflight = False
    from bellomberg.core.evidence_prompt_policy import select_template, diagnostic_block
    evidence_template = select_template(blackboard, 'red_team', RED_TEAM_PROMPT)
    from bellomberg.core.evidence_followup_policy import board_enabled, followup_block, project_receipts
    evidence_followup = board_enabled(blackboard)
    evidence_snapshot = None
    if saved_checkpoint is None and not trade_idea:
        user_msg += diagnostic_block(blackboard)
        if evidence_followup:
            user_msg += followup_block(blackboard)
            evidence_snapshot = project_receipts(getattr(blackboard, 'tool_receipts', None),
                                                 run_id=blackboard.weekly_store.run_id)
    elif evidence_followup:
        # The accepted request owns its evidence. Never project refreshed live receipts on replay.
        from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
        evidence_snapshot = deepcopy(saved_checkpoint.get('evidence_followup_snapshot'))
        if not isinstance(evidence_snapshot, dict):
            raise WeeklyRunBlocked('Red Team evidence checkpoint assente')
    try:
        messages = [{"role": "user", "content": user_msg}]
        if trade_idea:
            from bellomberg.core.trade_idea_contract import TRADE_IDEA_RED_TEAM_INSTRUCTIONS, RESEARCH_RED_TEAM_INSTRUCTIONS
            selected_system = prompt_for_language(RESEARCH_RED_TEAM_INSTRUCTIONS
                if is_research_mode(blackboard) else TRADE_IDEA_RED_TEAM_INSTRUCTIONS)
            # E7: il mandato del PM, come nei desk. Viene dalla fotografia della run (stessa
            # alla ripresa); entra nel contract: i checkpoint Red Team precedenti non si
            # riprendono e lo dicono (sotto).
            from bellomberg.agents.trade_idea import pm_constraints_text
            base_system = selected_system  # pre-E7: serve a riconoscere i checkpoint gia' pagati
            selected_system += "\n" + pm_constraints_text(blackboard)[1]
        else:
            selected_system = prompt_for_language(evidence_template)
            if is_research_mode(blackboard):
                selected_system += ('\nChallenge the exact sealed company research and R1 thesis: '
                    'observed statements, management guidance, analyst consensus and independent '
                    'assumptions remain distinct. Name material uncertainties and falsification '
                    'conditions. Missing consensus/documents must be declared. No workbook or AI '
                    'fair value is required; do not request compiler inputs or workbook repairs. '
                    'Mandate, prices, risk and sizing controls remain binding.')
        # MOD-CAP: il contratto e' il cap RICHIESTO; l'adattamento al provider avviene nel client.
        max_tokens = TRADE_IDEA_RED_MAX_TOKENS if trade_idea else WEEKLY_RED_MAX_TOKENS
        # Weekly: effort dal .env (RED_TEAM_EFFORT, default high), via llm_client.
        red_thinking = (role_thinking(blackboard, 'red_team') if trade_idea
                        else thinking_fase("red_team"))
        def contract_for(output_limit, thinking=None, system=None):
            return _checkpoint_digest({"version": 1, "model": MODEL_SYNTHESIZER,
                "system": selected_system if system is None else system,
                "tools": tools_schema, "iterations": 4,
                "max_tokens": output_limit,
                "thinking": red_thinking if thinking is None else thinking})
        contract = contract_for(max_tokens)
        start_iteration = 0
        pending_tools = {}
        inflight_tools = {}
        if saved_checkpoint is not None:
            if saved_checkpoint["contract"] != contract:
                # Only the exact previous policy is compatible. Keep its whole
                # loop at the original cap, including already journaled bodies.
                # A weekly loop started before effort high keeps its adaptive
                # reasoning too: the same paid bodies, never a mixed contract.
                legacy_cap = 65536 if trade_idea else 4200
                candidates = [(max_tokens, red_thinking), (legacy_cap, red_thinking)]
                if not trade_idea:
                    candidates += [(max_tokens, {"type": "adaptive"}), (legacy_cap, {"type": "adaptive"})]
                # E7 + review RV-E7 (P1): un checkpoint Trade Idea scritto PRIMA che il mandato
                # entrasse nel system resta riprendibile col suo system originale (body
                # identico a quello pagato: una critica gia' pagata non si perde), e la
                # ripresa si DICHIARA (data_gaps via trade_idea.pm_constraints_gaps).
                systems = [None] + ([base_system] if trade_idea else [])
                match = next(((system, cap, thinking) for system in systems for cap, thinking in candidates
                              if saved_checkpoint["contract"] == contract_for(cap, thinking, system)), None)
                if match is None and not trade_idea:
                    # MOD-CAP: il codice 05-06/10 salvava il contratto al cap GIA' tagliato al tetto
                    # del provider (_cap_del_provider). Oggi quel taglio lo fa il client sul filo:
                    # stesso corpo inviato, quindi stesso lavoro (pagato o no). Si riprende con quel
                    # cap, dichiarato. Il tetto si legge dal listino della run (registro richieste).
                    from bellomberg.core.llm_client import tetto_provider_letto
                    _tetto, _origine = tetto_provider_letto(MODEL_SYNTHESIZER)
                    if type(_tetto) is int and 0 < _tetto < WEEKLY_RED_MAX_TOKENS:
                        match = next(((None, _tetto, th) for th in (red_thinking, {"type": "adaptive"})
                                      if saved_checkpoint["contract"] == contract_for(_tetto, th)), None)
                        if match is not None:
                            print("[RED_TEAM] checkpoint col cap gia' adattato al provider (" + str(_tetto)
                                  + ", " + _origine + "): ripreso con quel cap (dichiarato)")
                if match is None:
                    raise ValueError("Red Team checkpoint contract changed"
                        + (" (system diverso da quello attuale e da quello precedente all'ingresso "
                           "del mandato del PM: non riprendibile, serve una nuova critica)"
                           if trade_idea else ""))
                if match[0] is not None:
                    selected_system = match[0]
                    blackboard.data["_red_team_system_pre_vincoli"] = True
                    print("[RED_TEAM] ripreso da checkpoint precedente alla cura E7: "
                          "mandato del PM non nel suo system (dichiarato)")
                max_tokens, red_thinking = match[1], match[2]
                contract = saved_checkpoint["contract"]
            if ("max_tokens" in saved_checkpoint
                    and (type(saved_checkpoint["max_tokens"]) is not int
                         or saved_checkpoint["max_tokens"] != max_tokens)):
                raise ValueError("Red Team checkpoint contract changed")
            from bellomberg.agents import chat_tools
            current_tools = [t for t in chat_tools.TOOL_DEFINITIONS if t["name"] in
                             ("get_portfolio_live", "get_portfolio_risk", "get_advanced_metrics")]
            if _checkpoint_digest(current_tools) != _checkpoint_digest(tools_schema):
                raise ValueError("Red Team tool contract changed")
            if saved_checkpoint["status"] == "complete":
                return saved_checkpoint["critique"]
            if saved_checkpoint["status"] in ("failed", "truncated"):
                raise ValueError("Red Team response incomplete: explicit review required")
            messages = deepcopy(saved_checkpoint["messages"])
            start_iteration = saved_checkpoint["iteration"]
            _usage, _usage_unknown = saved_checkpoint["usage"], saved_checkpoint["usage_unknown"]
            _calls = saved_checkpoint["calls"]
            pending_tools = deepcopy(saved_checkpoint.get("pending_tools", {}))
            inflight_tools = deepcopy(saved_checkpoint.get("inflight_tools", {}))
        if not trade_idea:   # V0-REDTEAM: preventivo prima di ogni invio (anche in ripresa)
            # MOD-CAP: validato col cap che partira' sul filo; il contratto resta quello richiesto.
            _preventivo_prima_dell_invio(MODEL_SYNTHESIZER, max_tokens)
        client = OpenRouterClient(timeout=timeout_specialisti(max_tokens), max_retries=0)
        if trade_idea:
            gate = getattr(blackboard, "budget_gate", None)
            if gate is None:
                raise ValueError("Trade Idea red team senza budget gate")
            client = gate.wrap_client(client, role="red_team")

        def checkpoint(event, state):
            state = _checkpoint_json(state)
            if checkpoint_key is not None:
                _persist_specialist_checkpoint(blackboard, checkpoint_key, state, event)
            return state

        def loop_state(iteration):
            return {"contract": contract, "max_tokens": max_tokens, "status": "running", "user_msg": user_msg,
                "tools_schema": tools_schema, "messages": messages, "iteration": iteration,
                "usage": _usage, "usage_unknown": _usage_unknown, "calls": _calls,
                "pending_tools": pending_tools, "inflight_tools": inflight_tools,
                **({"evidence_followup_snapshot": evidence_snapshot} if evidence_followup else {})}

        critique = ""
        critique_raw = ""
        # mini tool-loop (max 3 giri di verifica + risposta finale): il red team
        # VERIFICA i numeri che attacca invece di fidarsi o inventare
        _forced_final = False
        _it = start_iteration - 1
        for _it in range(start_iteration, 4):
            safe_snapshot = checkpoint("red_team_ready", loop_state(_it))
            _calls += 1
            _kw = {"tools": tools_schema} if tools_schema else {}
            if trade_idea:
                from bellomberg.core.trade_idea_contract import TRADE_IDEA_REVIEW_SCHEMA
                _kw["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "trade_idea_committee_review", "strict": True,
                    "schema": TRADE_IDEA_REVIEW_SCHEMA}}
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
            _request_inflight = True
            resp = client.messages.create(
                model=MODEL_SYNTHESIZER,
                # PM 02/10: 128k per nuovo lavoro; un loop verificato gia' iniziato
                # conserva il suo limite storico e gli stessi body di richiesta.
                # (Il 4200 -> 10000 del 02/10 per l'effort high e' assorbito dai 128k.)
                max_tokens=max_tokens,
                # Effort high (weekly): critica avversariale su tesi, dati e sizing. I token
                # di ragionamento contano nel tetto e in usage.reasoning_tokens — se la
                # critica esce troncata, il WARN qui sotto lo dice e il tetto si alza.
                # Trade Idea: effort della policy di esecuzione congelata.
                thinking=red_thinking,
                system=selected_system,
                messages=messages,
                **_kw,
            )
            _request_inflight = False
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
                        tool_key = _checkpoint_digest({"iteration": _it, "id": block.id,
                                                       "name": block.name, "input": block.input})
                        if tool_key in pending_tools:
                            results.append(deepcopy(pending_tools[tool_key]))
                            continue
                        if tool_key in inflight_tools:
                            raise ValueError("Red Team tool outcome unknown: " + block.name)
                        inflight_tools[tool_key] = {"name": block.name, "id": block.id}
                        checkpoint("red_team_tool_dispatch", {**safe_snapshot,
                            "pending_tools": pending_tools, "inflight_tools": inflight_tools})
                        print(f"[RED_TEAM] -> {block.name}({str(block.input)[:60]})")
                        r, receipt_truncated = None, False
                        try:
                            from bellomberg.agents import chat_tools; import json as _j
                            # V6 Lotto 3 (review B4): attribuzione del chiamante
                            r = chat_tools.dispatch(block.name, block.input or {},
                                                    caller="red-team",
                                                    **_as_of_freschezza(block.name, blackboard))
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
                                receipt_truncated = True
                                r_str = r_str[:_TETTO] + "...[truncated]"
                        except Exception as te:
                            r_str = f"[tool error dichiarato: {te}]"
                        if trade_idea:
                            from bellomberg.agents.specialists.base import _trade_idea_tool_receipt_success
                            blackboard.tool_receipts.append({"tool": block.name, "input": block.input or {},
                                "source": r.get("_source") if isinstance(r, dict) else None,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "success": _trade_idea_tool_receipt_success(r, block.name, truncated=receipt_truncated),
                                "output": r_str, "truncated": receipt_truncated})
                            blackboard.tool_log.append({"tool": block.name, "specialist": "red_team",
                                "time": datetime.now(timezone.utc).isoformat()})
                        result = {"type": "tool_result", "tool_use_id": block.id, "content": r_str}
                        pending_tools[tool_key] = result
                        inflight_tools.pop(tool_key, None)
                        checkpoint("red_team_tool", {**safe_snapshot,
                            "pending_tools": pending_tools, "inflight_tools": inflight_tools})
                        results.append(result)
                messages.append({"role": "user", "content": results})
                checkpoint("red_team_turn", loop_state(_it + 1))
                continue
            # audit/11 §5: concatena TUTTI i blocchi text (stessa classe di bug di
            # specialists/base: la coda 'I 3 RISCHI...' e' la prima a perdersi)
            _parts = [b.text for b in resp.content if hasattr(b, "text")]
            critique = "\n".join(p for p in _parts if p)
            critique_raw = critique
            if trade_idea:
                blackboard.data["_red_team_native_terminal"] = {
                    "response_id": getattr(resp, "id", None),
                    "stop_reason": getattr(resp, "stop_reason", None),
                    "text_present": bool(critique.strip()), "text_chars": len(critique)}
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
            if critique and _forced_final and not trade_idea:
                critique = ("[CRITICA AL LIMITE VERIFICHE (4 giri): tool esauriti, "
                            "buchi dichiarati nel testo]\n" + critique)
            break
        # §9-bis n.5: MAI una critica vuota zitta nel blackboard (fallback 14/07) —
        # possibile ormai solo se anche il giro forzato non produce testo
        if not critique:
            critique = SEGNAPOSTO_NESSUNA_CRITICA
            print("[RED_TEAM] WARN: critica vuota, scritto il buco dichiarato")
        native_issue = motivo_critica_non_utilizzabile(critique)
        evidence_review = None
        if evidence_followup:
            import json as _json
            evidence_review = _review_evidence_claims(critique_raw, evidence_snapshot)
            critique += ('\n\n=== RISCONTRO RED TEAM: CAMPI ESPLICITI, PROSA NON VALUTATA ===\n'
                         + _json.dumps(evidence_review, ensure_ascii=False, sort_keys=True, allow_nan=False))
        # P1 14/07: via Blackboard.write (round 1 = post-R1) invece dell'assegnazione
        # diretta: cosi' la critica PERSISTE nel DB (gate dedicato in base.write) e
        # regenerate_memo non rigenera piu' il memo SENZA red team.
        try:
            blackboard.write("_red_team", 1, critique)
        except Exception:
            if checkpoint_key is not None:
                raise
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
            if checkpoint_key is not None:
                raise
        issue = native_issue
        if getattr(resp, "stop_reason", None) != "end_turn":
            issue = issue or "terminal response incomplete: " + str(getattr(resp, "stop_reason", None))
        checkpoint("red_team_report", {**loop_state(_it + 1),
            "status": "failed" if issue else "complete", "critique": critique,
            **({"critique_raw": critique_raw, "evidence_review": evidence_review} if evidence_followup else {})})
        if issue and checkpoint_key is not None:
            raise ValueError("Red Team incomplete: " + issue)
        print(f"[RED_TEAM] critica generata: {len(critique)} chars")
        _cost = (_entry or {}).get("cost_eur")
        print(f"[RED_TEAM] usage: in={_usage['in'] if _usage['in'] is not None else 'n.d.'} out={_usage['out'] if _usage['out'] is not None else 'n.d.'} "
              f"cache_read={_usage['cache_read'] if _usage['cache_read'] is not None else 'n.d.'} cache_write={_usage['cache_write'] if _usage['cache_write'] is not None else 'n.d.'} "
              f"({_calls} call API, costo {_fmt_cost(_cost)})")
        return critique
    except Exception as e:
        if _request_inflight:
            _usage = _somma_usage(_usage, None)
            _usage_unknown = True
            request_id = getattr(e, "request_id", None)
            if request_id and request_id not in _usage.get("request_ids", []):
                _usage.setdefault("request_ids", []).append(request_id)
        callback = getattr(blackboard, "record_run_failure", None)
        if callable(callback):
            callback(e, desk="red_team", round_n=1)
        print(f"[RED_TEAM] API error (procedo senza): {e}")
        # Audit 11/09 (Fable 5.1, run 10/09 memo #53): il 403 del modello (gate 18+ di
        # OpenRouter) restava SOLO nel log; il registro `_red_team` non veniva scritto,
        # il Capo leggeva «chiave assente: non risulta girato» e il memo diceva al PM che
        # il red team «non ha girato» — falso nella causa. Il guasto si dichiara NEL
        # registro (il classificatore lo riconosce: Capo e desk R2 leggono la causa vera).
        try:
            blackboard.write("_red_team", 1, SEGNAPOSTO_NON_DISPONIBILE
                             + " errore API sul modello " + str(MODEL_SYNTHESIZER) + " — "
                             + str(e)[:300] + "]")
        except Exception as we:
            print(f"[RED_TEAM] registro del guasto non scritto (procedo): {we}")
        # anche qui i token gia' spesi vanno dichiarati, con lo status dell'errore
        # (regola no-fallback-silenziosi PM 14/07): un agente fallito non sparisce
        # dai costi. Nested try: il red team non deve MAI far cadere la run.
        try:
            blackboard.record_usage("_red_team", 1, MODEL_SYNTHESIZER, _usage,
                                    duration_s=round(_time.perf_counter() - _t0, 2),
                                    api_calls=_calls, cache_ttl=None, status="api_error")
        except Exception as ue:
            print(f"[RED_TEAM] usage non registrato (procedo): {ue}")
        if checkpoint_key is not None:
            raise
        return ""
