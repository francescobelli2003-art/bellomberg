"""Read-only, bounded evidence from the persisted filing archive."""
import re
import heapq
import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher

from bellomberg.core.paths import SQLITE_PATH

logger = logging.getLogger(__name__)


_VARIANTI = ("annuale", "semestrale", "trimestrale")


MAX_PAGINA = 20  # pagina massima di get_filing_changes (rinvio dei cambiamenti omessi)


ORDINI = ("documento", "punteggio")


# Schema e normalizzazione condivisi dal tool di chat e da quello di riserva (agent_tools).
FILING_CHANGES_PROPERTIES = {
    "ticker": {"type": "string"},
    "da": {"type": "integer", "minimum": 1, "description": "primo cambiamento da mostrare (C<n>), per i cambiamenti omessi dal contesto"},
    "max_changes": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5, "description": "quanti cambiamenti mostrare da `da` (pagina; 20 per vedere i cambiamenti omessi dal contesto)"},
    "variante": {"type": "string", "enum": ["annuale", "semestrale", "trimestrale"]},
    "ordine": {"type": "string", "enum": ["documento", "punteggio"], "default": "documento", "description": "\"punteggio\": dal cambiamento piu' importante (stesso punteggio del contesto; `da` = rango); \"documento\": per posizione"},
    "run_id": {"type": "integer", "minimum": 1, "description": "run del confronto citato nel contesto (\"run N\" nella riga di stato e nei rinvii); senza, l'ultimo confronto"},
}


def _intero(v, predefinito):
    v = predefinito if v is None else v
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        v = int(v)
    return v


def get_filing_changes_da_input(tool_input):
    """get_filing_changes dai parametri grezzi di un tool: i valori non validi restano tali e
    producono il payload "errore" di get_filing_changes, mai eccezioni."""
    return get_filing_changes(tool_input.get("ticker"), da=_intero(tool_input.get("da"), 1),
                              max_changes=_intero(tool_input.get("max_changes"), 5),
                              variante=tool_input.get("variante"), ordine=tool_input.get("ordine"),
                              run_id=_intero(tool_input.get("run_id"), None))


def _emittente(x):
    x = str(x or "").strip().upper()
    return "CIK:" + str(int(x[4:])) if x.startswith("CIK:") and x[4:].isdigit() else x


def _altro_emittente(run, riga_profilo):
    """L'emittente con cui e' girato `run`, se diverso da quello del profilo corrente (ri-collegamento)."""
    if not run or not riga_profilo:
        return None
    prima = _emittente((run.get("profile") or {}).get("emittente_id"))
    ora = _emittente((riga_profilo.get("profile") or {}).get("emittente_id"))
    return prima if prima and ora and prima != ora else None


def get_filing_changes(ticker, *, db_path=None, max_changes=5, da=1, variante=None, ordine=None, run_id=None,
                       pref_path=None):
    """No acquisition, valuation, model call or archive mutation is performed.

    `da` = primo cambiamento mostrato (C<n>, o rango con ordine="punteggio"); `max_changes` =
    ampiezza della pagina (1..20); `variante` = confronto di una variante non primaria;
    `ordine` = "documento" (posizione) o "punteggio" (stesso punteggio del contesto, a parita'
    posizione). Gli ID restano posizionali (C<n>). `run_id` = run del confronto citato nel
    contesto (deve essere del ticker, concluso e con un confronto); senza, l'ultimo confronto.
    Titoli esclusi, collegamenti annullati e preferenze illeggibili: "non_disponibile" col motivo.
    """
    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,29}", ticker):
        return {"ticker": ticker, "status": "errore", "reason": "ticker non valido", "_source": "filing_archive"}
    if isinstance(da, bool) or not isinstance(da, int) or da < 1:
        return {"ticker": ticker, "status": "errore", "reason": "da deve essere un intero >= 1",
                "_source": "filing_archive"}
    if isinstance(max_changes, bool) or not isinstance(max_changes, int) or not 1 <= max_changes <= MAX_PAGINA:
        return {"ticker": ticker, "status": "errore", "reason": f"max_changes deve essere un intero tra 1 e {MAX_PAGINA}",
                "_source": "filing_archive"}
    if ordine is None:
        ordine = "documento"
    if not isinstance(ordine, str) or ordine not in ORDINI:
        return {"ticker": ticker, "status": "errore", "reason": 'ordine deve essere "documento" o "punteggio"',
                "_source": "filing_archive"}
    if variante is not None and variante not in _VARIANTI:
        return {"ticker": ticker, "status": "errore", "reason": "variante non valida",
                "_source": "filing_archive"}
    if run_id is not None and (isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1):
        return {"ticker": ticker, "status": "errore", "reason": "run_id deve essere un intero >= 1",
                "_source": "filing_archive"}
    from bellomberg.storage.filing_store import FilingStore
    try:
        store = FilingStore(db_path or SQLITE_PATH)
        profile = store.get_profile(ticker)
        runs = store.list_runs(ticker, limit=20)
        # Stessa selezione della scheda: ultimo run con confronto; un errore successivo a parte.
        last, errore = store.confronto_ed_errore(ticker)
        scelto = store.get_run(run_id) if run_id is not None else None
    except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        return {"ticker": ticker, "status": "non_disponibile", "reason": str(exc),
                "_source": "filing_archive"}
    # Revisione G1: le scelte dell'utente valgono anche qui, come nel contesto (schede_filing).
    # I run dell'emittente rifiutato restano in archivio: senza questo controllo lo specialista
    # li riceveva con status "ok" dopo «Scollega».
    from bellomberg.core.language import text
    from bellomberg.storage import filing_preferenze
    try:
        pref = filing_preferenze.carica(pref_path)
    except (ValueError, OSError) as exc:
        return {"ticker": ticker, "status": "non_disponibile", "_source": "filing_archive",
                "reason": text("preferenze filing illeggibili: collegamenti annullati ed esclusioni non "
                               "verificabili, nessun confronto fornito (", "filing preferences unreadable: removed "
                               "links and exclusions cannot be checked, no comparison returned (") + _una(exc, 200) + ")"}
    if ticker in (pref.get("esclusi") or []):
        return {"ticker": ticker, "status": "non_disponibile", "_source": "filing_archive",
                "reason": text("titolo escluso dal controllo filing dall'utente: i confronti in archivio non sono evidenza",
                               "ticker excluded from filing checks by the user: archived comparisons are not evidence")}
    if profile and filing_preferenze.e_scollegato(pref, ticker, profile["version"]):
        return {"ticker": ticker, "status": "non_disponibile", "_source": "filing_archive",
                "reason": text("collegamento annullato dall'utente (Scollega): i confronti in archivio sono "
                               "dell'emittente rifiutato", "link removed by the user (Scollega): archived "
                               "comparisons belong to the rejected issuer")}
    extra = {}
    if run_id is not None:
        risultato = (scelto or {}).get("result") or {}
        motivo = ("run inesistente" if not scelto else
                  "run di un altro titolo" if scelto["ticker"] != ticker else
                  "run non concluso" if scelto["status"] in ("queued", "running") else
                  "run senza confronto" if risultato.get("controllo_leggero") or not (
                      risultato.get("confronto_corrente") or risultato.get("confronto_storico")) else None)
        if motivo:
            return {"ticker": ticker, "status": "errore", "reason": f"run_id {run_id}: {motivo}",
                    "_source": "filing_archive"}
        if last and last["id"] != scelto["id"]:
            extra["latest_run_id"] = last["id"]  # il contesto citava un confronto poi superato
        last = scelto
    precedente = _altro_emittente(last, profile)
    if precedente:
        return {"ticker": ticker, "status": "non_disponibile", "_source": "filing_archive", "run_id": last["id"],
                "reason": text(f"confronto di un collegamento precedente ({precedente}), non dell'emittente collegato "
                               "ora: non e' evidenza (si attende il primo confronto del nuovo collegamento)",
                               f"comparison from a previous link ({precedente}), not the issuer linked now: not "
                               "evidence (waiting for the first comparison of the new link)")}
    active = next((r for r in runs if r["status"] in ("queued", "running")), None)
    if not last:
        return {"ticker": ticker, "status": "non_disponibile", "reason": "nessun confronto concluso",
                "profile_status": "configurato" if profile else "assente",
                "active_run": active["id"] if active else None, "_source": "filing_archive"}
    result = last.get("result") or {}
    current = result.get("confronto_corrente")
    historical = result.get("confronto_storico")
    diff = current or historical or {}
    coppia_selezionata = result.get("coppia")
    if variante and variante != result.get("variante"):
        altra = next((v for v in result.get("varianti") or []
                      if isinstance(v, dict) and v.get("tipo") == variante and v.get("confronto")), None)
        if not altra:
            return {"ticker": ticker, "status": "non_disponibile", "reason": "variante assente",
                    "run_id": last["id"], "_source": "filing_archive"}
        current, historical, diff = altra["confronto"], None, altra["confronto"]
        coppia_selezionata = altra.get("coppia")
    changes = diff.get("cambiamenti") or []
    shown = []
    sequenza = [(n, c, None) for n, c in enumerate(changes, 1)]
    if ordine == "punteggio":
        sequenza = sorted(((n, c, punteggio(c)) for n, c in enumerate(changes, 1) if isinstance(c, dict)),
                          key=lambda x: (-x[2], x[0]))
    for rango, (n, change, valore) in enumerate(sequenza, 1):
        if rango < da:
            continue
        if len(shown) == max_changes:
            break
        excerpts = {}
        for side in ("prima", "dopo"):
            c = change.get(side)
            if isinstance(c, dict):
                excerpts[side] = {k: c.get(k) for k in ("url", "sha256", "sezione", "pagine_fisiche", "inizio", "fine")}
                raw = str(c.get("testo") or "")
                excerpts[side]["testo"] = raw[:220]
                excerpts[side]["testo_troncato"] = len(raw) > 220
                excerpts[side]["citation_id"] = f"C{n}-{side}"
        voce = {"tipo": change.get("tipo"), "estratti": excerpts}
        if valore is not None:
            voce.update(rank=rango, score=valore)
        shown.append(voce)
    judgment = last.get("judgment") or {}
    index = last.get("index") or {}
    findings = judgment.get("findings") or []
    motivations = result.get("motivi") or []
    similarities = diff.get("similarita_sezioni") or {}
    recente = next((r for r in runs if r["status"] not in ("queued", "running")), None)
    if _leggero(recente) and recente["id"] > last["id"]:
        extra["last_check"] = {"at": recente.get("finished_at"), "esito": "nessun deposito nuovo"}
    if errore:
        extra["last_error"] = {"at": errore.get("finished_at"), "reason": _una(errore.get("reason"), 300)}
    return {"ticker": ticker, "status": last["status"], "reason": last.get("reason"),
            "run_id": last["id"], "finished_at": last.get("finished_at"), **extra,
            "first_shown": da, "order": ordine, "variant": variante or result.get("variante"),
            "active_run": active["id"] if active else None,
            "scope": "corrente" if current else "storico" if historical else "nessun confronto",
            "latest_unverified": bool(result.get("ultimo_non_verificato")),
            "freshness": result.get("freschezza") or {"stato": "n.d."},
            "coverage": result.get("copertura") or {"stato": "n.d."},
            "pair": {k: {"url": (coppia_selezionata.get(k) or {}).get("url"),
                         "sha256": (coppia_selezionata.get(k) or {}).get("sha256"),
                         "metadati": (coppia_selezionata.get(k) or {}).get("metadati")}
                     for k in ("prima", "dopo")} if coppia_selezionata else None,
            "pair_status": "disponibile" if coppia_selezionata else "non_disponibile",
            "diff_status": diff.get("stato") or "non_disponibile",
            "sections": diff.get("sezioni_confrontate") or [],
            "similarity": dict(list(similarities.items())[:8]),
            "similarity_sections_total": len(similarities),
            "changes": shown, "changes_shown": len(shown), "changes_total": len(changes),
            # limiti del confronto (p.es. modificati non abbinati: un rimosso + un aggiunto riformulato)
            "diff_limits": [str(m)[:200] for m in (diff.get("limiti") or [])[:8]],
            "unpaired_changes": _non_abbinati(diff),
            "truncation": "estratti limitati a 220 caratteri; aprire /filings/runs/{run_id} per il testo completo" if shown else None,
            "judgment_status": judgment.get("status") or "n.d.",
            "judgment_reason": judgment.get("reason"),
            "findings": [{"category": f.get("category"), "assessment": str(f.get("assessment") or "")[:500],
                          "citations": (f.get("citations") or [])[:5]}
                         for f in findings[:3] if isinstance(f, dict)],
            "findings_total": len(findings),
            "findings_truncated": len(findings) > 3 or any(
                len(str(f.get("assessment") or "")) > 500 or len(f.get("citations") or []) > 5
                for f in findings[:3] if isinstance(f, dict)),
            "index_status": index.get("status") or "n.d.",
            "index_reason": index.get("reason"),
            "limitations": [str(m)[:300] for m in motivations[:5]],
            "limitations_total": len(motivations),
            "limitations_truncated": len(motivations) > 5 or any(len(str(m)) > 300 for m in motivations[:5]),
            "_source": "filing_archive"}


# --- Contesto v2: una scheda per titolo, impaginazione deterministica a budget (spec §7.2) ---

MAX_CARATTERI = 14_000
ESTRATTO = 220
LATO_MODIFICATO = 110
PESI = {"rischi": 3.0, "gestione": 2.0, "contenziosi": 2.0}
SIMBOLI = {"aggiunto": "+", "rimosso": "−", "modificato": "~", "spostato": "↔"}
ETICHETTA_PERIODO = {"annuale": ("anno al", "year to"), "semestrale": ("semestre al", "half year to"),
                     "trimestrale": ("trimestre al", "quarter to"), "nove_mesi": ("nove mesi al", "nine months to")}
_ERRORI_ARCHIVIO = (FileNotFoundError, RuntimeError, sqlite3.DatabaseError, json.JSONDecodeError)
_SIM_CARATTERI = 40_000  # oltre (lunghezza × lunghezza dei tratti diversi) la similarità passa alle parole


def _peso(sezione):
    s = str(sezione or "").lower()
    if s in PESI:
        return PESI[s]
    if ("risk" in s or "rischi" in s) and "market" not in s and "mercato" not in s:
        return 3.0
    if any(k in s for k in ("md&a", "management", "gestione", "legal", "contenzios")):
        return 2.0
    return 1.0


def _norm(t):
    return " ".join(str(t or "").split())


def _una(t, n=None):
    """Testo libero su una sola riga (mai righe contraffatte nel contesto), poi troncato."""
    t = _norm(t)
    return t if n is None else t[:n]


def _estratto(t):
    """Estratto su una riga, senza virgolette che possano chiudere la citazione (stessa lunghezza)."""
    return _norm(t).replace("«", "‹").replace("»", "›")


def _similarita(a, b):
    """Rapporto stile SequenceMatcher e indice della prima differenza.

    Prefisso e suffisso comuni si contano per intero; il tratto diverso si confronta
    a caratteri, o a parole (pesate in caratteri) se troppo lungo: costo limitato
    anche con molti paragrafi modificati.
    """
    if not a and not b:
        return 1.0, 0
    p = 0
    limite = min(len(a), len(b))
    while p < limite and a[p] == b[p]:
        p += 1
    s = 0
    while s < limite - p and a[-1 - s] == b[-1 - s]:
        s += 1
    ma, mb = a[p:len(a) - s], b[p:len(b) - s]
    if len(ma) * len(mb) <= _SIM_CARATTERI:
        uguali = sum(m.size for m in SequenceMatcher(None, ma, mb, autojunk=False).get_matching_blocks())
    else:
        ta, tb = ma.split(), mb.split()
        uguali = 0
        for op, i1, i2, j1, j2 in SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
            if op == "equal":
                uguali += sum(len(x) + 1 for x in ta[i1:i2])
            elif op == "replace":
                xa, xb = " ".join(ta[i1:i2]), " ".join(tb[j1:j2])
                if len(xa) * len(xb) <= _SIM_CARATTERI:
                    uguali += sum(m.size for m in SequenceMatcher(None, xa, xb, autojunk=False).get_matching_blocks())
    return 2.0 * (p + s + uguali) / (len(a) + len(b)), p


def _valuta(c):
    prima, dopo = (c.get("prima") or {}), (c.get("dopo") or {})
    a, b = _norm(prima.get("testo"))[:4000], _norm(dopo.get("testo"))[:4000]
    primo = 0
    if c.get("prima") and c.get("dopo"):
        # Modificato (e spostato, testo identico): conta solo quanto è cambiato.
        ratio, primo = _similarita(a, b)
        fattore, lunghezza = max(0.0, min(1.0, 1.0 - ratio)), max(len(a), len(b))
    else:
        fattore, lunghezza = 1.0, len(b or a)
    url = str((dopo or prima).get("url") or "")
    # solo testo ESEF: repository o pacchetto ufficiale dal sito dell'emittente (fase F)
    esef_url = url.startswith("https://filings.xbrl.org/") or re.search(r"\.(?:xbri|zip)$", url.split("?", 1)[0], re.I)
    prosa = _prosa(b or a) if esef_url else 1.0
    valore = round(_peso((dopo or prima).get("sezione")) * fattore * min(1.0, lunghezza / 400) * prosa, 4)
    return valore, primo


_PAROLA = re.compile(r"^[a-zà-ÿ][a-zà-ÿ'’\-]*[.,;:]?$")


def _prosa(t):
    """Fattore 0,1–1 per i soli testi ESEF: quota di parole minuscole (anche brevi) sui token.
    Le righe di tabella impaginate come testo (bilanci convertiti da PDF: intestazioni e cifre)
    restano sotto 0,35; la prosa, anche con cifre, sta sopra 0,5 e vale per intero (prova
    reale fase B, 2026-10-03)."""
    token = t.split()
    if not token:
        return 1.0
    quota = sum(bool(_PAROLA.match(x)) for x in token) / len(token)
    return max(0.1, min(1.0, (quota - 0.2) / 0.3))


def punteggio(cambiamento):
    """Peso sezione × tipo (aggiunto/rimosso 1; modificato 1 − similarità) × min(1, lunghezza/400)
    × prosa (solo testi ESEF: le righe di tabella valgono meno)."""
    return _valuta(cambiamento)[0]


def _taglia(t, n=ESTRATTO):
    t = _estratto(t)
    return t if len(t) <= n else t[:n - 1] + "…"


def _finestra(t, i):
    da = max(0, min(i, len(t)) - LATO_MODIFICATO // 3)
    pezzo = t[da:da + LATO_MODIFICATO - 2]
    return ("…" if da else "") + pezzo + ("…" if da + len(pezzo) < len(t) else "")


def _riga_cambiamento(n, c, primo):
    tipo = c.get("tipo")
    if c.get("prima") and c.get("dopo"):
        a, b = _estratto(c["prima"].get("testo"))[:4000], _estratto(c["dopo"].get("testo"))[:4000]
        sezione = _una(c["dopo"].get("sezione"), 60)
        if tipo == "spostato":
            return f"{SIMBOLI['spostato']} {sezione} «{_taglia(b)}» [C{n}-prima, C{n}-dopo]"
        return f"{SIMBOLI.get(tipo, '~')} {sezione} «{_finestra(a, primo)}» → «{_finestra(b, primo)}» [C{n}-prima, C{n}-dopo]"
    lato = "dopo" if c.get("dopo") else "prima"
    cit = c.get(lato) or {}
    return f"{SIMBOLI.get(tipo, '?')} {_una(cit.get('sezione'), 60)} «{_taglia(cit.get('testo'))}» [C{n}-{lato}]"


def _istante(valore):
    """datetime consapevole del fuso (un valore ingenuo è ora locale), o None."""
    if isinstance(valore, str):
        try:
            valore = datetime.fromisoformat(valore)
        except ValueError:
            return None
    if not isinstance(valore, datetime):
        return None
    return valore if valore.tzinfo else valore.astimezone()


def _data(iso):
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return _una(iso or "?", 20)


def _giorno(iso):
    t = _istante(iso)
    return t.astimezone().strftime("%d/%m") if t else "?"


def _pct(v):
    from bellomberg.core.language import text
    cifra = f"{abs(v):.1f}"
    return ("+" if v >= 0 else "−") + text(cifra.replace(".", ","), cifra) + "%"


def _cifre(n):
    """Intero con separatore delle migliaia della lingua (1.421 / 1,421)."""
    from bellomberg.core.language import text
    return text(f"{n:,}".replace(",", "."), f"{n:,}")


def _riga_numeri(numeri):
    from bellomberg.core.language import text
    if not numeri:
        return text("numeri non disponibili: non calcolati per questo confronto",
                    "figures not available: not computed for this comparison")
    if numeri.get("stato") != "ok":
        motivo = _una(numeri.get("motivo") or numeri.get("stato"), 120)
        return text("numeri non disponibili: ", "figures not available: ") + motivo
    def periodo(v, lato):
        p = (v.get("periodi") or {}).get(lato) or {}
        modo = v.get("tipo_periodo")
        if modo == "durata" and p.get("inizio") and p.get("fine"):
            return _una(f"{p['inizio']}/{p['fine']}", 50)
        if modo == "istante" and p.get("fine"):
            return text("istante ", "instant ") + _una(p["fine"], 25)
        return text("periodo non dichiarato", "period not stated")

    voci = " · ".join(
        f"{_una(str(v.get('voce')).replace('_', ' '), 40)} {_pct(v['delta_pct'])}"
        + f" [{periodo(v, 'dopo')} vs {periodo(v, 'prima')}; "
        + (_una(v.get("valuta"), 20) or text("unita non dichiarata", "unit not stated")) + "]"
        for v in numeri.get("voci") or [] if v.get("delta_pct") is not None)
    if not voci:
        return text("numeri non disponibili: nessuna voce confrontabile",
                    "figures not available: no comparable item")
    coda = text(f" (variante {_una(numeri['variante'], 20)})", f" ({_una(numeri['variante'], 20)} variant)") \
        if numeri.get("variante") else ""
    coda += text(" · fonte numeri: ", " · figures source: ") + (
        _una(numeri.get("fonte"), 120) or text("non dichiarata", "not stated"))
    # Fase F: emittente nuovo, numeri sul trimestre precedente e non sull'anno prima.
    testa = text("numeri vs trimestre precedente: ", "figures vs previous quarter: ") \
        if numeri.get("confronto") == "trimestre_precedente" else text("numeri ", "figures ")
    # Revisione 04/10 (R7): voci non confrontabili (valute diverse, periodo mancante) dichiarate.
    # R-FONTI v2 (riserva MEDIO-2): comparativi rideterminati dal deposito piu' recente, dichiarati nella riga
    rideterminati = [r for r in numeri.get("rideterminazioni") or [] if isinstance(r, dict) and r.get("voce")]
    if rideterminati:
        coda += text(" · rideterminati nel deposito piu' recente (vale il deposito, companyfacts diverso): ",
                     " · restated in the latest filing (filing value used, companyfacts differs): ") + _una(
            ", ".join(str(r["voce"]).replace("_", " ")[:40] for r in rideterminati))  # al piu' le 6 VOCI
    scarti = [s for s in numeri.get("scarti") or [] if isinstance(s, dict)]
    if scarti:
        elenco = _una(", ".join(f"{str(s.get('voce')).replace('_', ' ')} ({s.get('motivo')})" for s in scarti))
        coda += text(" · non confrontate: ", " · not compared: ") + (
            elenco if len(elenco) <= 160 else elenco[:159] + "…")  # taglio visibile: elenco intero nel tool
    return testa + voci + coda


def _fonte(profilo, variante):
    from bellomberg.core.language import text
    from urllib.parse import urlsplit
    v_ir = next((x for x in profilo.get("varianti") or [] if isinstance(x, dict) and x.get("tipo") == variante), None)
    ir = (v_ir or {}) if (v_ir or {}).get("fonti") == ["ir"] else profilo if (
        not profilo.get("varianti") and profilo.get("fonti") == ["ir"]) else None
    if ir is not None:
        # Profilo o variante da PDF IR (fase C): host della prima pagina indicata.
        try:
            host = urlsplit(str((ir.get("ir_urls") or [""])[0])).hostname
        except ValueError:
            host = None
        origine = text(" (proposta AI)", " (AI proposal)") if (ir.get("proposta_ai") or profilo.get("proposta_ai")) else ""
        return f"IR {_una(host, 60) or 'PDF'}{origine}"
    emittente = str(profilo.get("emittente_id") or "")
    cik = _una(profilo.get("cik"), 20) or (_una(emittente[4:], 20).zfill(10) if emittente.startswith("CIK:") else None)
    if cik:
        varianti = [v for v in profilo.get("varianti") or [] if isinstance(v, dict)]
        v = next((x for x in varianti if x.get("tipo") == variante), None)
        forme = (v or {}).get("forme_sec") or profilo.get("forme_sec") \
            or list(dict.fromkeys(f for x in varianti for f in x.get("forme_sec") or []))
        forme = [_una(f, 12) for f in forme if _una(f)]
        return f"SEC {'/'.join(forme)} CIK {cik}" if forme else f"SEC CIK {cik}"
    lei = _una(profilo.get("lei"), 40) or (_una(emittente[4:], 40) if emittente.startswith("LEI:") else None)
    if lei:
        return f"ESEF LEI {lei}"
    return text("profilo manuale", "manual profile")


def _non_abbinati(diff):
    """Modificati forse non abbinati (rimosso + aggiunto): limite globale dei run precedenti
    o finestra locale dei tratti molto lunghi."""
    from bellomberg.market_data.filing_diff import LIMITE_COPPIE, LIMITE_FINESTRA
    limiti = (diff or {}).get("limiti") or []
    return LIMITE_COPPIE in limiti or LIMITE_FINESTRA in limiti


def _sezioni_profilo(profilo, variante):
    """Nomi delle sezioni attese dal profilo per la variante (vuoto se non deducibili)."""
    if not isinstance(profilo, dict):
        return []
    v = next((x for x in profilo.get("varianti") or [] if isinstance(x, dict) and x.get("tipo") == variante), None)
    sezioni = (v or {}).get("sezioni") or profilo.get("sezioni")
    return sorted(sezioni) if isinstance(sezioni, dict) else []


def _leggero(run):
    return bool(run) and bool((run.get("result") or {}).get("controllo_leggero"))


def _in_errore(run):
    return bool(run) and run.get("status") in ("errore", "non_disponibile")


def _successivo(ultimo, run):
    if not ultimo or not run:
        return False
    if isinstance(ultimo.get("id"), int) and isinstance(run.get("id"), int):
        return ultimo["id"] > run["id"]
    a, b = _istante(ultimo.get("finished_at")), _istante(run.get("finished_at"))
    return bool(a and b and a > b)


def _scheda_vuota(ticker, stato, *, fonte=None, fresco=None):
    """`fresco`: "aggiornato" / "non_aggiornato" / "senza_confronto" (None: nessun profilo)."""
    return {"ticker": ticker, "gruppo": 3, "stato": stato, "numeri": None, "cambiamenti": [],
            "totale_cambiamenti": 0, "limite": 2, "peso": 0.0, "altra_variante": None,
            "fonte": fonte, "fresco": fresco, "run_id": None, "confronto_at": None, "ultimo_errore": None}


def _chiave_coppia(result):
    """(sha prima, sha dopo) della coppia confrontata, o None se non dichiarata per intero."""
    coppia = (result or {}).get("coppia") or {}
    chiave = tuple((coppia.get(k) or {}).get("sha256") for k in ("prima", "dopo"))
    return chiave if all(isinstance(x, str) and x for x in chiave) else None


def _arg_run(run):
    rid = (run or {}).get("id") if isinstance(run, dict) else run
    return f"run_id={rid}, " if isinstance(rid, int) and not isinstance(rid, bool) else ""


def _coppia_dal_sito(result):
    """Fase F: un documento della coppia confrontata viene da un pacchetto del sito dell'emittente
    (non basta una riga del sito nel catalogo: revisione finale)."""
    coppia = (result or {}).get("coppia") or {}
    for lato in ("prima", "dopo"):
        url = str((coppia.get(lato) or {}).get("url") or "").split("?", 1)[0]
        if url and not url.startswith("https://filings.xbrl.org/") and re.search(r"\.(?:xbri|zip)$", url, re.I):
            return True
    return False


def scheda_da_run(ticker, *, profilo, run, ultimo, escluso=False, freschezza, novita_dopo, errore=None,
                  coppia_al_memo=None, ultimo_completo=None):
    """Scheda di un titolo: riga di stato, numeri, cambiamenti punteggiati (tutti, ordinati).

    NOVITÀ: confronto concluso dopo `novita_dopo` su una coppia di documenti diversa da
    `coppia_al_memo` (coppia del confronto corrente a quell'istante; None = nessuno).
    """
    from bellomberg.core.language import text
    ticker = _una(ticker, 30)
    non_agg = text("NON AGGIORNATO: ", "NOT UPDATED: ")
    f = freschezza or {}
    if escluso:
        return _scheda_vuota(ticker, f"{ticker} · " + text("escluso dal controllo", "excluded from checks"))
    if profilo is None:
        return _scheda_vuota(ticker, f"{ticker} · " + text(
            "non disponibile: nessun profilo (attivabile dalla pagina Filing)",
            "not available: no profile (can be enabled from the Filing page)"))
    if run is None:
        # Nessun confronto: la fonte elenca le forme di tutte le varianti del profilo.
        variante = profilo.get("tipo") if not profilo.get("varianti") else None
        if _in_errore(ultimo):
            motivo = _una(ultimo.get("reason") or ultimo.get("status"), 100)
        elif (ultimo_completo or {}).get("reason"):
            # Run completo concluso senza coppia (es. repository ESEF fermo), anche se dopo c'e'
            # stato un controllo leggero: si dice perche' manca il confronto.
            motivo = text("nessun confronto: ", "no comparison: ") + _una(ultimo_completo["reason"], 140)
        else:
            motivo = text("in attesa del primo confronto", "waiting for the first comparison")
        fonte = _fonte(profilo, variante)
        stato = f"{ticker} · {fonte} · " + text("non disponibile: ", "not available: ") + motivo
        if f.get("stato") == "non_aggiornato":
            stato += " · " + non_agg + _una(f.get("motivo") or text("motivo non dichiarato", "reason not stated"), 160)
        return _scheda_vuota(ticker, stato, fonte=fonte, fresco="senza_confronto")

    result = run.get("result") or {}
    variante = result.get("variante") or (run.get("profile") or {}).get("tipo") or profilo.get("tipo")
    coppia = result.get("coppia") or {}
    fine = {k: ((coppia.get(k) or {}).get("metadati") or {}).get("periodo_fine") for k in ("prima", "dopo")}
    etichetta = text(*ETICHETTA_PERIODO.get(variante, ("periodo al", "period to")))
    fonte = _fonte(profilo, variante)
    if _coppia_dal_sito(result):
        fonte += text(" dal sito dell'emittente", " from the issuer's website")  # fase F
    parti = [ticker, fonte]
    if fine["dopo"]:
        parti.append(f"{etichetta} {_data(fine['dopo'])} vs {_data(fine['prima'])}")
        if coppia.get("regola") == "sequenziale":
            parti.append(text("trimestre su trimestre (manca l'anno prima)", "quarter on quarter (no prior year)"))
    else:
        parti.append(text("periodo non dichiarato", "period not stated"))
    parti.append(text("confronto del ", "compared on ") + _giorno(run.get("finished_at")))
    if isinstance(run.get("id"), int):
        parti.append(f"run {run['id']}")  # stesso run nei rinvii a get_filing_changes
    # `fresco` segue lo stesso ramo che scrive la freschezza nella riga (panoramica coerente).
    fresco = "non_aggiornato"
    if f.get("stato") == "non_aggiornato":
        parti.append(non_agg + _una(f.get("motivo") or text("motivo non dichiarato", "reason not stated"), 160))
    elif _in_errore(ultimo) and _successivo(ultimo, run):
        parti.append(non_agg + text("ultimo controllo in errore: ", "latest check failed: ")
                     + _una(ultimo.get("reason") or ultimo.get("status"), 100))
    elif f.get("stato") == "aggiornato" and _leggero(ultimo) and _successivo(ultimo, run):
        nota = ""
        if errore and _successivo(errore, run):  # errore dopo il confronto, poi controllo leggero
            motivo = _una(errore.get("reason") or errore.get("status"), 100)
            nota = text(f"; ultimo errore il {_giorno(errore.get('finished_at'))}: {motivo}",
                        f"; latest error on {_giorno(errore.get('finished_at'))}: {motivo}")
        parti.append(text(f"aggiornato (controllato il {_giorno(ultimo.get('finished_at'))}, nessun deposito nuovo{nota})",
                          f"up to date (checked on {_giorno(ultimo.get('finished_at'))}, no new filing{nota})"))
        fresco = "aggiornato"
    elif f.get("stato") == "aggiornato":
        parti.append(text("aggiornato", "up to date"))
        fresco = "aggiornato"
    else:
        parti.append(text("freschezza non dichiarata", "freshness not stated"))
    corrente = result.get("confronto_corrente")
    diff = corrente or result.get("confronto_storico") or {}
    fermo = next((f for f in result.get("fonti") or [] if isinstance(f, dict) and f.get("fermo")), None)
    if not corrente and fermo:
        # Repository ESEF fermo: l'ultimo documento e' verificato, ma e' vecchio.
        anno = _una(str((result.get("coppia") or {}).get("dopo", {}).get("metadati", {}).get("periodo_fine") or "")[:4], 4)
        parti.append(text(f"confronto storico: repository ESEF fermo all'esercizio FY{anno}",
                          f"historical comparison: ESEF repository stuck at FY{anno}"))
    elif not corrente:
        parti.append(text("confronto storico: ultimo documento non verificato",
                          "historical comparison: latest document unverified"))
    confrontate = diff.get("sezioni_confrontate")
    nessuna = isinstance(confrontate, list) and not confrontate and not diff.get("cambiamenti")
    if diff.get("stato") == "parziale" and nessuna:
        parti.append(text("confronto parziale: nessuna sezione confrontata",
                          "partial comparison: no section compared"))
    elif diff.get("stato") == "parziale" and isinstance(confrontate, list):
        mancanti = [_una(x, 30) for x in _sezioni_profilo(run.get("profile") or profilo, variante)
                    if x not in confrontate]
        parti.append(text("confronto parziale", "partial comparison") + (
            text(": non confrontate ", ": not compared ") + ", ".join(mancanti) if mancanti else ""))
    elif diff.get("stato") not in (None, "ok"):
        parti.append(text("confronto ", "comparison ") + _una(diff.get("stato"), 40))
    if _non_abbinati(diff):
        # Documenti lunghi: i modificati restano un rimosso e un aggiunto quasi identici.
        parti.append(text("rimossi e aggiunti non abbinati (troppi segmenti): un − e un + possono essere "
                          "lo stesso testo riformulato",
                          "removed and added not paired (too many segments): a − and a + may be "
                          "the same reworded text"))
    fatto, soglia = _istante(run.get("finished_at")), _istante(novita_dopo)
    chiave = _chiave_coppia(result)
    # Lo stesso confronto ricalcolato (rifacimento, /A, profilo cambiato) non e' una novita'.
    nuovo = bool(fatto and soglia and fatto > soglia) and (chiave is None or chiave != coppia_al_memo)
    if nuovo:
        parti.append(text("NOVITÀ", "NEW"))

    cambiamenti = []
    for n, c in enumerate(diff.get("cambiamenti") or [], 1):
        if not isinstance(c, dict):
            continue
        valore, primo = _valuta(c)
        cambiamenti.append({"riga": _riga_cambiamento(n, c, primo), "punteggio": valore, "pos": n})
    cambiamenti.sort(key=lambda c: (-c["punteggio"], c["pos"]))

    altre = []
    for v in result.get("varianti") or []:
        if not isinstance(v, dict) or v.get("primaria") or not v.get("confronto"):
            continue
        p = list(v.get("coppia_periodi") or [None, None]) + [None, None]
        tipo, quanti = _una(v.get("tipo"), 20), len(v["confronto"].get("cambiamenti") or [])
        altre.append(text(f"variante {tipo}: ", f"{tipo} variant: ") + f"{_data(p[1])} vs {_data(p[0])} · "
                     + text(f"{_cifre(quanti)} cambiamenti", f"{_cifre(quanti)} changes")
                     + f' → get_filing_changes({ticker}, {_arg_run(run)}variante="{tipo}")')
    return {"ticker": ticker, "gruppo": (0 if nuovo else 1) if cambiamenti else 2,
            "stato": " · ".join(parti), "numeri": _riga_numeri(result.get("numeri")),
            "cambiamenti": cambiamenti, "totale_cambiamenti": len(cambiamenti),
            "limite": 4 if nuovo else 2, "peso": round(sum(c["punteggio"] for c in cambiamenti), 4),
            "altra_variante": "\n".join(altre) or None, "fonte": fonte, "fresco": fresco,
            "run_id": run.get("id") if isinstance(run.get("id"), int) else None,
            "confronto_at": run.get("finished_at"),
            # Prova reale fase E (04/10/2026): un confronto senza nessuna sezione trovata non e'
            # «invariato» ma un profilo da sistemare (intestazioni non riconosciute).
            "ultimo_errore": ({"at": run.get("finished_at"),
                               "reason": text("nessuna sezione confrontata: ", "no section compared: ")
                               + _una("; ".join(str(m) for m in diff.get("motivi") or []), 300)}
                              if diff.get("stato") == "parziale" and nessuna else None)}


def impagina(schede, *, max_caratteri=MAX_CARATTERI, intestazione):
    """Testo del contesto entro il budget (v. _impagina).

    La lingua si legge UNA volta per chiamata (cantiere zero rossi 05/10): senza contesto ogni
    text() rileggeva preferences.json dal disco (~3.500 letture su 80 titoli, l'85% del tempo).
    Un language_context gia' attivo resta quello (nessuna lettura); stessi testi IT ed EN.
    """
    from bellomberg.core.language import capture_language, language_context
    with language_context(capture_language()):
        return _impagina(schede, max_caratteri=max_caratteri, intestazione=intestazione)


def _impagina(schede, *, max_caratteri, intestazione):
    """Testo del contesto entro il budget.

    Base: per titolo fino a `limite` cambiamenti (per punteggio). Se la base entra, lo spazio
    residuo si riempie a giri: in ogni giro un titolo aggiunge al più `limite` cambiamenti,
    scelti uno alla volta fra tutti i titoli per punteggio (pari: gruppo, ordine dei titoli,
    posizione); al primo che non entra ci si ferma. Se la base non entra si tolgono
    cambiamenti dal fondo, poi le righe dei numeri. Righe di stato, citazioni e rinvii a
    get_filing_changes restano sempre; il piede dichiara i troncamenti e il conteggio
    esatto dei caratteri del testo.
    """
    from bellomberg.core.language import text
    ordine = sorted(schede, key=lambda s: (s["gruppo"], -s["peso"], s["ticker"]))
    mostrati = [min(s["limite"], len(s["cambiamenti"])) for s in ordine]
    con_numeri = [bool(s.get("numeri")) for s in ordine]

    cifre = _cifre

    def puntatore(i, k):
        s = ordine[i]
        omessi = max(0, s["totale_cambiamenti"] - k)
        if not omessi:
            return ""
        return (text(f"altri {cifre(omessi)} cambiamenti omessi", f"{cifre(omessi)} more changes omitted")
                + f' → get_filing_changes({s["ticker"]}, {_arg_run(s.get("run_id"))}ordine="punteggio", '
                f'da={k + 1}, max_changes={MAX_PAGINA})')

    def blocco(i):
        s, k = ordine[i], mostrati[i]
        righe = [s["stato"]] + ([s["numeri"]] if con_numeri[i] else [])
        righe += [c["riga"] for c in s["cambiamenti"][:k]]
        omessi = max(0, s["totale_cambiamenti"] - k)
        if omessi:
            righe.append(puntatore(i, k))
        if s.get("altra_variante"):
            righe.append(s["altra_variante"])
        return "\n".join(righe), omessi

    def piede(omessi, titoli, n, oltre):
        riga = text(f"TRONCAMENTI: {cifre(omessi)} cambiamenti omessi su {titoli} {'titolo' if titoli == 1 else 'titoli'}; "
                    f"{cifre(n)}/{cifre(max_caratteri)} caratteri.",
                    f"TRUNCATIONS: {cifre(omessi)} changes omitted across {titoli} ticker{'' if titoli == 1 else 's'}; "
                    f"{cifre(n)}/{cifre(max_caratteri)} characters.")
        # Revisione 04/10 (R7): le righe dei numeri tolte per budget si dichiarano coi titoli.
        tolte = [ordine[i]["ticker"] for i in range(len(ordine)) if ordine[i].get("numeri") and not con_numeri[i]]
        if tolte:
            riga += text(f" Righe dei numeri omesse: {', '.join(tolte)}.", f" Figures lines omitted: {', '.join(tolte)}.")
        return riga + (text(" Solo righe di stato: oltre il budget.", " Status lines only: over budget.")
                       if oltre else "")

    def totale(oltre=False):
        # Punto fisso: il piede dichiara la lunghezza del testo che lo contiene.
        base = corpo + 1
        n = base
        while True:
            m = base + len(piede(omessi, titoli, n, oltre))
            if m == n:
                return n
            n = m

    blocchi = [blocco(i) for i in range(len(ordine))]
    corpo = len(intestazione) + sum(len(b) + 1 for b, _ in blocchi)
    omessi = sum(o for _, o in blocchi)
    titoli = sum(1 for _, o in blocchi if o)

    def rifai(i):
        nonlocal corpo, omessi, titoli
        vecchio, o_vecchio = blocchi[i]
        blocchi[i] = nuovo = blocco(i)
        corpo += len(nuovo[0]) - len(vecchio)
        omessi += nuovo[1] - o_vecchio
        titoli += bool(nuovo[1]) - bool(o_vecchio)

    def riempi():
        # Lunghezze incrementali (riga + rinvio): nessun blocco ricostruito a ogni passo.
        nonlocal corpo, omessi, titoli

        def lp(i, k):
            p = puntatore(i, k)
            return len(p) + 1 if p else 0

        while True:
            quota = [s["limite"] for s in ordine]

            def candidato(i):
                c = ordine[i]["cambiamenti"][mostrati[i]]
                return (-c["punteggio"], ordine[i]["gruppo"], i, c["pos"])
            coda = [candidato(i) for i in range(len(ordine))
                    if quota[i] > 0 and mostrati[i] < len(ordine[i]["cambiamenti"])]
            if not coda:
                return
            heapq.heapify(coda)
            while coda:
                i = heapq.heappop(coda)[2]
                s, k = ordine[i], mostrati[i]
                delta = len(s["cambiamenti"][k]["riga"]) + 1 + lp(i, k + 1) - lp(i, k)
                prima = max(0, s["totale_cambiamenti"] - k)
                dopo = max(0, s["totale_cambiamenti"] - k - 1)
                corpo += delta
                omessi += dopo - prima
                titoli += bool(dopo) - bool(prima)
                if totale() > max_caratteri:  # il prossimo non entra: ci si ferma (prevedibile)
                    corpo -= delta
                    omessi -= dopo - prima
                    titoli -= bool(dopo) - bool(prima)
                    return
                mostrati[i] += 1
                quota[i] -= 1
                if quota[i] > 0 and mostrati[i] < len(s["cambiamenti"]):
                    heapq.heappush(coda, candidato(i))

    # 0) base entro il budget: si riempie lo spazio residuo per punteggio, a giri
    if totale() <= max_caratteri:
        riempi()
        for i in range(len(ordine)):
            blocchi[i] = blocco(i)
    # 1) cambiamenti dal fondo: ultimo titolo, punteggio più basso
    for i in reversed(range(len(ordine))):
        while mostrati[i] > 0 and totale() > max_caratteri:
            mostrati[i] -= 1
            rifai(i)
    # 2) righe dei numeri dal fondo, solo se ancora oltre
    for i in reversed(range(len(ordine))):
        if totale() <= max_caratteri:
            break
        if con_numeri[i]:
            con_numeri[i] = False
            rifai(i)
    oltre = totale() > max_caratteri
    n = totale(oltre)
    testo = "\n".join([intestazione] + [b for b, _ in blocchi] + [piede(omessi, titoli, n, oltre)])
    return {"testo": testo, "caratteri": len(testo), "budget": max_caratteri, "omessi_totali": omessi,
            "righe": {s["ticker"]: {"testo": b, "caratteri": len(b), "omessi": o}
                      for s, (b, o) in zip(ordine, blocchi)}}


def _freschezza_archivio(riga_profilo, ultimo, ora=None):
    """Aggiornato = ultimo run concluso entro interval_hours del profilo."""
    from bellomberg.core.language import text
    if riga_profilo is None:
        return None
    if not ultimo:
        return {"stato": "non_aggiornato", "motivo": text("nessun controllo concluso", "no completed check")}
    fatto = _istante(ultimo.get("finished_at"))
    ore = riga_profilo.get("interval_hours") or 168
    if fatto and (ora or datetime.now(timezone.utc)) - fatto <= timedelta(hours=ore):
        return {"stato": "aggiornato", "motivo": None}
    return {"stato": "non_aggiornato",
            "motivo": text("ultimo controllo del ", "latest check on ") + _giorno(ultimo.get("finished_at"))}


def schede_filing(tickers, *, db_path=None, freschezza=None, novita_dopo=None, pref_path=None):
    """Una scheda per ticker (ordine originale, senza duplicati), sola lettura dell'archivio."""
    from bellomberg.core.language import text
    from bellomberg.storage import filing_preferenze
    from bellomberg.storage.filing_store import FilingStore
    unici = list(dict.fromkeys(_una(t, 30) for t in tickers if isinstance(t, str) and _una(t)))
    pref_errore = None
    try:
        pref = filing_preferenze.carica(pref_path)
    except (ValueError, OSError) as exc:
        # Revisione G1: prima si procedeva senza esclusioni ne' scollegamenti (solo log), e un
        # titolo scollegato mostrava di nuovo l'emittente rifiutato. Ora e' dichiarato per titolo.
        logger.warning("preferenze filing illeggibili, nessun confronto nel contesto: %s", exc)
        pref, pref_errore = {"esclusi": [], "rifiutati": {}}, exc
    esclusi = set(pref.get("esclusi") or [])

    def guasto(t, exc):
        scheda = _scheda_vuota(t, f"{t} · " + text("non disponibile: archivio filing: ",
                                                   "not available: filing archive: ") + _una(exc, 160))
        return {**scheda, "guasto": _una(exc, 300)}
    try:
        store = FilingStore(db_path or SQLITE_PATH)
    except _ERRORI_ARCHIVIO as exc:
        return [guasto(t, exc) for t in unici]
    if pref_errore is not None:
        return [{**_scheda_vuota(t, f"{t} · " + text(
            "non disponibile: preferenze filing illeggibili (collegamenti annullati ed esclusioni non verificabili): ",
            "not available: filing preferences unreadable (removed links and exclusions cannot be checked): ")
            + _una(pref_errore, 160)), "guasto": _una(pref_errore, 300)} for t in unici]
    schede = []
    for t in unici:
        if t in esclusi:
            schede.append(scheda_da_run(t, profilo=None, run=None, ultimo=None, escluso=True,
                                        freschezza=None, novita_dopo=None))
            continue
        try:
            riga = store.get_profile(t)
            runs = store.list_runs(t, limit=20)
        except _ERRORI_ARCHIVIO as exc:
            schede.append(guasto(t, exc))
            continue
        if riga and filing_preferenze.e_scollegato(pref, t, riga["version"]):
            # Collegamento annullato dall'utente: mai i confronti di un emittente rifiutato.
            schede.append(_scheda_vuota(t, f"{t} · " + text(
                "non disponibile: collegamento annullato (da ricollegare dalla pagina Filing)",
                "not available: link removed (can be relinked from the Filing page)")))
            continue
        conclusi = [r for r in runs if r["status"] not in ("queued", "running")]
        try:  # stessa selezione di get_filing_changes, stato e panoramica
            run, errore = store.confronto_ed_errore(t)
        except _ERRORI_ARCHIVIO as exc:
            schede.append(guasto(t, exc))
            continue
        con_confronto = bool(run) and bool((run.get("result") or {}).get("confronto_corrente")
                                           or (run.get("result") or {}).get("confronto_storico"))
        run = run if con_confronto else None
        precedente = _altro_emittente(run, riga)
        if precedente:
            # Ri-collegamento: i run dell'emittente di prima restano in archivio (seguito revisione G1).
            schede.append(_scheda_vuota(t, f"{t} · " + text(
                f"non disponibile: l'ultimo confronto e' di un collegamento precedente ({precedente}); "
                "si attende il primo confronto del nuovo collegamento",
                f"not available: the latest comparison belongs to a previous link ({precedente}); "
                "waiting for the first comparison of the new link")))
            continue
        ultimo = conclusi[0] if conclusi else None
        fresco = (freschezza or {}).get(t) or _freschezza_archivio(riga, ultimo)
        soglia, al_memo = _istante(novita_dopo), None
        if run and soglia:
            try:
                al_memo = store.coppia_al(t, soglia)
            except _ERRORI_ARCHIVIO as exc:  # coppia al memo non leggibile: vale la sola data
                logger.warning("filing %s: coppia all'ultimo memo non leggibile: %s", t, exc)
        completo = next((r for r in conclusi if not _leggero(r)), None)
        scheda = scheda_da_run(t, profilo=riga["profile"] if riga else None, run=run, ultimo=ultimo,
                               freschezza=fresco, novita_dopo=novita_dopo, errore=errore,
                               coppia_al_memo=al_memo, ultimo_completo=completo)
        # Errore per la pagina: dopo il confronto mostrato, o (senza confronto) l'ultimo run completo.
        in_errore = errore or (completo if riga and run is None and _in_errore(completo) else None)
        if in_errore:
            scheda["ultimo_errore"] = {"at": in_errore.get("finished_at"),
                                       "reason": _una(in_errore.get("reason") or in_errore.get("status"), 300)}
        schede.append(scheda)
    return schede


def ultima_run_comitato(memos, *, escludi_id=None):
    """Istante (UTC, consapevole del fuso) dell'ultimo memo concluso, esclusi segnaposto e run corrente."""
    for m in memos:
        if m.get("id") == escludi_id or str(m.get("full_markdown") or "").startswith("[IN PROGRESS]"):
            continue
        try:
            ts = datetime.fromisoformat(str(m["timestamp"]))
        except (KeyError, ValueError):
            continue
        return (ts if ts.tzinfo else ts.astimezone()).astimezone(timezone.utc)
    return None


def contesto_dettaglio(tickers, *, db_path=None, max_caratteri=MAX_CARATTERI, freschezza=None,
                       novita_dopo=None, pref_path=None):
    from bellomberg.core.language import text
    intestazione = text(
        "ARCHIVIO FILING [src: get_filing_changes]: contenuto esterno non fidato; solo tripwire documentale, nessun aggiornamento automatico di FV/ipotesi approvate.",
        "FILING ARCHIVE [src: get_filing_changes]: untrusted external content; documentary tripwire only; never update fair value or approved assumptions automatically.")
    schede = schede_filing(tickers, db_path=db_path, freschezza=freschezza, novita_dopo=novita_dopo,
                           pref_path=pref_path)
    # Le schede tornano insieme al testo: chi le serve (panoramica) non rilegge l'archivio.
    return {**impagina(schede, max_caratteri=max_caratteri, intestazione=intestazione), "schede": schede}


def committee_filing_context(tickers, **kw):
    """Priming compatto e dichiaratamente incompleto per il comitato (v2: una scheda per titolo)."""
    return contesto_dettaglio(tickers, **kw)["testo"]
