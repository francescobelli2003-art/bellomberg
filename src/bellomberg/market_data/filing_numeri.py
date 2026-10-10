"""Numeri chiave della coppia confrontata dal filing.

Stessi periodi del confronto testuale, fonte XBRL ufficiale (SEC companyfacts,
us-gaap o ifrs-full). Deterministico: solo voci presenti in entrambi i periodi,
nessuna stima e nessuna somma di trimestri.
"""
from bellomberg.core.language import text
from bellomberg.market_data.sec_xbrl import CANONICAL

VOCI = (("ricavi", "revenue", "durata"), ("utile_operativo", "operating_income", "durata"),
        ("utile_netto", "net_income", "durata"), ("scorte", "inventory", "istante"),
        ("debito", "lt_debt", "istante"), ("flusso_cassa_operativo", "cfo", "durata"))


def _valore(facts, tags, periodo, modo):
    inizio, fine = periodo
    for tassonomia, nomi in (("us-gaap", tags[0]), ("ifrs-full", tags[1])):
        for nome in nomi:
            unita = facts.get("facts", {}).get(tassonomia, {}).get(nome, {}).get("units", {})
            for valuta, righe in unita.items():
                for r in righe:
                    if r.get("end") == fine and (modo == "istante" or r.get("start") == inizio):
                        return float(r["val"]), valuta, f"{tassonomia}:{nome}"
    return None, None, None


def _periodo_testo(periodo, modo):
    return periodo[1] if modo == "istante" else f"{periodo[0]}/{periodo[1]}"


def variazioni(facts, prima, dopo):
    """Variazioni delle voci chiave tra due periodi (inizio, fine) ISO.

    Revisione 04/10 (R7): ogni voce non confrontata finisce in `scarti` col motivo (assente in un
    periodo, valute diverse) e ogni voce porta valuta e tag XBRL usati; nessun taglio della lista.
    """
    voci, scarti = [], []
    for voce, chiave, modo in VOCI:
        a, va, ta = _valore(facts, CANONICAL[chiave], prima, modo)
        b, vb, tb = _valore(facts, CANONICAL[chiave], dopo, modo)
        if a is None or b is None:
            mancanti = [_periodo_testo(p, modo) for p, x in ((prima, a), (dopo, b)) if x is None]
            scarti.append({"voce": voce, "motivo": text("assente nel periodo " + " e nel periodo ".join(mancanti),
                                                        "missing in period " + " and in period ".join(mancanti))})
            continue
        if va != vb:
            scarti.append({"voce": voce, "motivo": text(f"valute diverse fra i periodi ({va} -> {vb}): non confrontabile",
                                                        f"different currencies between periods ({va} -> {vb}): "
                                                        "not comparable")})
            continue
        delta = round((b - a) / abs(a) * 100, 1) if a else None
        if delta is None:  # REV_G2a R-6: prima restava fra le voci e il contesto la toglieva zitto
            scarti.append({"voce": voce, "motivo": text(f"base zero nel periodo {_periodo_testo(prima, modo)}: "
                                                        "variazione non calcolabile",
                                                        f"zero base in period {_periodo_testo(prima, modo)}: "
                                                        "change not computable")})
        riga = {"voce": voce, "prima": a, "dopo": b, "delta_pct": delta, "valuta": va, "tag": tb}
        if ta != tb:
            riga["tag_prima"] = ta  # perimetro forse diverso: dichiarato
        voci.append(riga)
    valute = {v["valuta"] for v in voci}
    out = {"stato": "ok" if voci else "vuoto", "valuta": voci[0]["valuta"] if len(valute) == 1 else None,
           "voci": voci, "scarti": scarti}
    if len(valute) > 1:
        elenco = ", ".join(sorted(valute))
        out["avvisi"] = [text(f"voci in valute diverse: {elenco} (valuta per voce)",
                              f"items in different currencies: {elenco} (currency per item)")]
    return out


def numeri_per_coppia(cik, coppia):
    """Numeri chiave per la coppia di documenti scelta dalla pipeline."""
    from bellomberg.market_data import sec_xbrl
    cik = str(cik).zfill(10)

    def periodo(lato):
        return tuple(coppia[lato]["metadati"][k] for k in ("periodo_inizio", "periodo_fine"))

    fine = periodo("dopo")[1]
    facts = sec_xbrl._fetch_companyfacts(cik)
    nota = None
    if facts and not _ha_fine(facts, fine):
        # La cache (7 giorni) puo' precedere il deposito confrontato: una sola rilettura dalla rete.
        # Revisione 04/10: la cache e' condivisa col DCF, mai cancellata; la rilettura la
        # sostituisce solo se riesce.
        nuovi = sec_xbrl._fetch_companyfacts(cik, forza=True)
        if nuovi:
            facts = nuovi
            nota = "companyfacts riletto dalla SEC: la cache era anteriore al periodo confrontato"
        else:
            nota = "companyfacts riletto senza esito (rete o SEC): resta la cache precedente"
            # REV_G2a R-4: anche il DCF deve sapere che la cache precede un deposito pubblicato
            sec_xbrl.segna_cache_superata(cik, f"manca il periodo chiuso il {fine}, rilettura fallita")
    if not facts:
        return {"stato": "non_disponibile", "motivo": "companyfacts non disponibile", "voci": [],
                "fonte": "SEC companyfacts"}
    if not _ha_fine(facts, fine):
        motivo = f"companyfacts non contiene ancora il periodo chiuso il {fine}" + (f" ({nota})" if nota else "")
        return _numeri_ixbrl(cik, coppia, facts, periodo("prima"), periodo("dopo"), motivo)
    out = {**variazioni(facts, periodo("prima"), periodo("dopo")), "fonte": "SEC companyfacts",
           "origine": "companyfacts"}
    if nota:
        out["nota_fonte"] = nota
    return out


def _numeri_ixbrl(cik, coppia, facts_cf, prima, dopo, motivo_cf):
    """Ripiego dichiarato (R-FONTI 10/10, Opus 5.5): companyfacts non ha ancora il periodo del documento
    verificato; se il documento scaricato e' inline XBRL, i numeri vengono dai suoi fatti (stesse VOCI e
    stesse guardie: emittente del CIK, periodo esatto, unita'/valuta per voce). Il periodo precedente resta
    quello di companyfacts quando coincide (entro l'arrotondamento dichiarato); se il documento lo
    RIDETERMINA vale il documento (stessa base dei due lati, come nello storico ESEF) e si dichiara per voce
    (riserva MEDIO-2 v2). Documento non iXBRL (6-K) o conversione fallita: «non_aggiornato» col motivo."""
    from bellomberg.market_data import sec_xbrl
    lato = (coppia or {}).get("dopo") or {}
    accn = sec_xbrl.accession_da_url(lato.get("url"))
    base = {"voci": [], "fonte": "SEC companyfacts", "origine": "companyfacts"}
    if not lato.get("path") or not sec_xbrl.e_ixbrl(lato["path"]):
        return {**base, "stato": "non_aggiornato",
                "motivo": motivo_cf + "; documento del periodo senza inline XBRL: nessun fatto da convertire"}
    concetti = sec_xbrl._concetti_canonici({chiave for _, chiave, _ in VOCI})
    meta = {"accn": accn}
    try:
        letto = sec_xbrl.fatti_ixbrl_documento(lato["path"], cik, meta=meta)
    except Exception as exc:
        return {**base, "stato": "non_aggiornato",
                "motivo": motivo_cf + f"; iXBRL del deposito {accn} non convertito ({type(exc).__name__}: "
                                      f"{str(exc)[:160]})"}
    fonte = sec_xbrl.fonte_ixbrl(accn)
    if letto["periodo_documento"] and dopo[1] not in letto["periodo_documento"]:
        return {**base, "stato": "non_aggiornato",
                "motivo": motivo_cf + f"; {fonte}: periodo del documento {', '.join(letto['periodo_documento'])} "
                                      f"diverso dal periodo confrontato {dopo[1]}"}
    if not _ha_fine({"facts": letto["facts"]}, dopo[1]):
        return {**base, "stato": "non_aggiornato",
                "motivo": motivo_cf + f"; {fonte}: nessun fatto dell'emittente chiuso il {dopo[1]}"}
    uniti, rideterminati = sec_xbrl.unisci_fatti((facts_cf or {}).get("facts") or {}, letto["facts"],
                                                 fonte_base="SEC companyfacts", fonte_aggiunta=fonte,
                                                 concetti=concetti)
    calcolo = variazioni({"facts": uniti}, prima, dopo)
    modi = {voce: (chiave, modo) for voce, chiave, modo in VOCI}
    rideterminazioni = []
    for v in calcolo["voci"]:  # fonte di ciascun lato, per voce
        chiave, modo = modi[v["voce"]]
        cf = _valore(facts_cf or {}, CANONICAL[chiave], prima, modo)[0]
        v["fonte_prima"] = "SEC companyfacts" if cf is not None and cf == v["prima"] else fonte
        v["fonte_dopo"] = fonte
        if cf is not None and cf != v["prima"]:
            v["rideterminato"] = {"valore_companyfacts": cf, "valore_usato": v["prima"], "fonte_usata": fonte}
            rideterminazioni.append({"voce": v["voce"], "fine": prima[1], "valore_precedente": cf,
                                     "fonte_precedente": "SEC companyfacts", "valore_usato": v["prima"],
                                     "fonte_usata": fonte})
    prima_cf = sorted({v["fonte_prima"] for v in calcolo["voci"]}) == ["SEC companyfacts"]
    out = {**calcolo, "fonte": fonte + (" (periodo precedente: SEC companyfacts)" if prima_cf else ""),
           "origine": sec_xbrl.ORIGINE_IXBRL,
           "fonti_periodi": {"prima": sorted({v["fonte_prima"] for v in calcolo["voci"]}), "dopo": fonte},
           "nota_fonte": motivo_cf + f": numeri del periodo dall'{fonte}",
           "conversione": {**letto["conversione"], "altre_entita": letto["altre_entita"]}}
    incoerenti = [x for x in letto["incoerenti"] if x["concetto"] in concetti]
    if incoerenti:
        out["incoerenti"] = incoerenti  # stesso fatto con valori diversi nel documento: tolto, dichiarato
    if rideterminazioni:
        out["rideterminazioni"] = rideterminazioni
        out.setdefault("avvisi", []).append(
            f"{len(rideterminazioni)} comparativi del periodo precedente rideterminati nel {fonte} "
            f"({', '.join(r['voce'] for r in rideterminazioni)}): vale il deposito piu' recente, companyfacts "
            "dichiarato accanto")
    if rideterminati:
        out["rideterminazioni_fatti"] = rideterminati  # tutti i fatti sostituiti, anche fuori dalle voci
    return out


def _ha_fine(facts, fine):
    for tassonomia in facts.get("facts", {}).values():
        for concetto in tassonomia.values():
            for righe in concetto.get("units", {}).values():
                if any(r.get("end") == fine for r in righe):
                    return True
    return False
