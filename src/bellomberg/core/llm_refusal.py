"""
llm_refusal.py — RIFIUTO DEL MODELLO DICHIARATO (regola PM 14/07: niente fallback zitti).

Perche' esiste (26/07, pre-run V6, chat backend Opus 5):
    Dallo swap modelli (changelog (48)) il comitato gira su claude-opus-5 e la
    fascia Sonnet su claude-sonnet-5. Questi modelli hanno i safeguard di sicurezza
    elevati: quando un classificatore declina una richiesta la chiamata NON solleva
    un'eccezione — torna un HTTP 200 regolare con `stop_reason == "refusal"` e
    `content` vuoto (o parziale se il rifiuto arriva a meta' stream).

    Conseguenza sul codice esistente: i retry (che intercettano le Exception) NON
    scattano, la concatenazione dei blocchi text produce stringa vuota, e il
    chiamante cade sul suo paracadute generico ("No output", lezione = "", critica
    vuota). Il buco c'e' ma la CAUSA sparisce: identico a un modello che non ha
    scritto nulla. E' esattamente il fallback silenzioso vietato dal PM.

    Questo modulo NON decide nulla e non ritenta: traduce la risposta in una riga
    di causa dichiarata, che ogni chiamante innesta nel suo canale (memo, blackboard,
    log run). Zero effetti su qualunque risposta normale: se stop_reason non e'
    "refusal" ritorna None e il flusso resta quello di sempre.

Fonte: doc API Anthropic (stop_reason "refusal" + stop_details, GA da Opus 4.7).
"""

# Prefisso unico: lo cercano a occhio nel memo/log e col grep nei collaudi.
REFUSAL_TAG = "RIFIUTO DEL MODELLO"


def refusal_reason(response, agente=""):
    """Ritorna la causa DICHIARATA se `response` e' un rifiuto, altrimenti None.

    Difensivo per costruzione: qualunque campo mancante (SDK piu' vecchio del
    campo stop_details, oggetto finto nei test) diventa "n.d." dichiarato, mai
    un'eccezione che tirerebbe giu' la run al posto della chiamata gia' pagata.
    """
    if getattr(response, "stop_reason", None) != "refusal":
        return None

    det = getattr(response, "stop_details", None)
    categoria = getattr(det, "category", None) if det is not None else None
    spiega = getattr(det, "explanation", None) if det is not None else None
    # stop_details puo' arrivare come dict su SDK che non lo tipizzano ancora
    if categoria is None and isinstance(det, dict):
        categoria = det.get("category")
        spiega = det.get("explanation")

    chi = ("[" + str(agente) + "] ") if agente else ""
    msg = ("[" + chi + REFUSAL_TAG + " (categoria: " + str(categoria or "n.d.") + "): "
           "la richiesta e' stata declinata dai safeguard del modello, NON e' un errore "
           "di rete e NON e' un output vuoto. Il contenuto di questo blocco e' assente "
           "per rifiuto, non per mancanza di dati.")
    if spiega:
        msg += " Motivazione API: " + str(spiega)[:300]
    return msg + "]"


def has_partial_text(response):
    """True se il rifiuto e' arrivato a meta' generazione (c'e' testo parziale gia' pagato).

    Serve ai chiamanti per DIRE che quello che si vede e' un troncone, non l'analisi.
    """
    try:
        for b in getattr(response, "content", None) or []:
            if getattr(b, "type", None) == "text" and (getattr(b, "text", "") or "").strip():
                return True
    except Exception:
        pass
    return False
