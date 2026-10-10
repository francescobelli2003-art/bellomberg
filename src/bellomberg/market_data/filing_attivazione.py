"""Attivazione dei profili filing: proposta -> profilo standard -> salvataggio.

Gratis e deterministico (solo fonti SEC pubbliche, nessuna AI). Il profilo e'
sempre salvato col ticker del portafoglio. I collegamenti ambigui restano
proposte; un errore su un titolo non ferma l'attivazione in blocco.
"""
from datetime import date

from bellomberg.market_data import filing_identita
from bellomberg.market_data.filing_identita import proponi
from bellomberg.market_data.filing_profili_auto import (forme_presenti, profilo_esef, profilo_sec, regex_confermata,
                                                         sonda_tipo_6k)
from bellomberg.storage import filing_preferenze

INTERVALLO_AUTO_ORE = 24


def _catalogo_default(ticker, cik=None):
    from bellomberg.market_data.sec_edgar import get_filing_catalog
    return get_filing_catalog(ticker, cik=cik)


def _attiva_esef(store, ticker, scelto, origine, nome, proposta, motivo, indice_fn, scopri_fn=None):
    """Profilo ESEF a blocchi: serve almeno un deposito con xBRL-JSON; lingua = inglese se c'e'.

    Fase F: senza depositi sul repository (o col repository fermo) si cercano i pacchetti
    ufficiali sul sito dell'emittente (l'attivazione e' un'azione dell'utente: rete ammessa)."""
    from bellomberg.market_data import esef, esef_sito
    from bellomberg.market_data.filing_esef import scegli_lingua
    try:
        righe = (indice_fn or esef.indice_depositi)(scelto["lei"])["righe"]
    except Exception as exc:
        return {"ticker": ticker, "esito": "errore", "proposta": proposta,
                "motivo": f"indice ESEF: {type(exc).__name__}: {exc}"[:300]}
    con_json = [r for r in righe if isinstance(r, dict) and r.get("json_url") and r.get("period_end")]
    ultimo = max((r["period_end"] for r in con_json), default=None)
    dal_sito, nota_sito, trovato = [], None, None
    if esef_sito.atteso_piu_recente(ultimo, date.today()) and esef_sito.consigliere_in_corso():
        # mai esplorare durante una run del Consigliere (revisione finale): ci pensa il giro dopo
        nota_sito = "sito dell'emittente non esplorato: run del Consigliere in corso (si riprova al controllo giornaliero)"
    elif esef_sito.atteso_piu_recente(ultimo, date.today()):
        try:
            trovato = (scopri_fn or esef_sito.scopri)(ticker, lei=scelto["lei"])
            lei = str(scelto["lei"]).upper()
            dal_sito = [p for p in trovato.get("pacchetti") or [] if p.get("lei") == lei]
            if not dal_sito:
                nota_sito = ("sito dell'emittente: " + "; ".join((trovato.get("motivi") or [])[:2])
                             if trovato.get("motivi") else "nessun pacchetto ESEF sul sito dell'emittente")
        except Exception as exc:
            nota_sito = f"sito dell'emittente: {esef_sito.motivo_eccezione(exc, 120)}"
    if not con_json and not dal_sito:
        motivo_esef = ("nessun deposito ESEF con xBRL-JSON sul repository filings.xbrl.org"
                       + (f"; {nota_sito}" if nota_sito else ""))
        if trovato is not None:  # sito esplorato: relazioni in PDF dal sito (decisione PM 05/10)
            return _attiva_sito(store, ticker, nome or scelto.get("nome"), proposta, motivo_esef, trovato=trovato,
                                lei=scelto.get("lei"))
        return {"ticker": ticker, "esito": "senza_fonte", "proposta": proposta, "motivo": motivo_esef}
    lingue_sito = {p.get("lingua") for p in dal_sito}
    lingua = scegli_lingua(con_json, "en") if con_json else ("en" if "en" in lingue_sito or None in lingue_sito
                                                              else sorted(lingue_sito)[0])
    profilo = profilo_esef(ticker, lei=scelto["lei"], nome=nome or scelto["nome"], origine=origine, lingua=lingua)
    salvato = store.set_profile(ticker, profilo, enabled=True, interval_hours=INTERVALLO_AUTO_ORE)
    esito = {"ticker": ticker, "esito": "attivato", "fonte": "esef", "profilo_versione": salvato["version"],
             "proposta": proposta, "motivo": motivo,
             "esercizi": sorted({r["period_end"][:4] for r in con_json} | {p["period_end"][:4] for p in dal_sito})}
    if dal_sito:
        esito["dal_sito"] = len(dal_sito)
    return esito


# ------------------------------------------------------------------ sito dell'emittente (V8B, 05/10)
# Decisione PM 05/10 (opzione B): un titolo senza documenti dall'archivio ufficiale (SEC/ESEF/OAM)
# prende le relazioni periodiche in PDF dal sito dell'emittente, per QUALUNQUE societa' (nessuna
# lista cablata). Profilo IR deterministico, senza AI, salvato SOLO se il PDF si verifica
# (emittente, periodo, tipo, lingua, perimetro); origine sempre dichiarata.
ORIGINE_COLLEGAMENTO_SITO = "sito_emittente"
# Regole del periodo in piu' per le relazioni dal sito (prova reale 06/10): provate DOPO quelle di
# ripiego di filing_proposta_ai, sempre misurate sul documento (_periodo_regge: periodo univoco e
# durata del tipo). Gruppi ammessi da filing_verifica._periodo_testuale.
_DATA_EN_SITO = r"[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\.?\s+[A-Za-zä]+\s+\d{4}"
_PERIODI_SITO = {
    "semestrale": [
        # «1/1–30/6/2026», «1.1.–30.6.2026»: estremi numerici con l'anno in comune
        r"(?P<giorno_inizio>\d{1,2})[./](?P<mese_inizio>\d{1,2})\.?\s*[–-]\s*(?P<giorno_fine>\d{1,2})[./]"
        r"(?P<mese_fine>\d{1,2})\s*[./]\s*(?P<anno>\d{4})",
        # titolo da semestrale e, poco dopo, la data di chiusura («Half-Yearly … up to 30 June 2026»)
        rf"(?P<mesi>half)[-\s]*year(?:ly)?\b[\s\S]{{0,1500}}?\b(?:as\s+(?:of|at)|up\s+to|ended)\s+(?P<fine>{_DATA_EN_SITO})",
    ],
    "annuale": [
        rf"(?P<inizio>(?:January\s+1|1\.?\s+January),?\s+\d{{4}})\s*(?:to|until|through|–|-)\s*"
        rf"(?P<fine>(?:December\s+31|31\.?\s+December),?\s+\d{{4}})",
        r"(?P<giorno_inizio>\d{1,2})[./](?P<mese_inizio>\d{1,2})\.?\s*[–-]\s*(?P<giorno_fine>\d{1,2})[./]"
        r"(?P<mese_fine>\d{1,2})\s*[./]\s*(?P<anno>\d{4})",
    ],
    "trimestrale": [
        r"(?P<giorno_inizio>\d{1,2})[./](?P<mese_inizio>\d{1,2})\.?\s*[–-]\s*(?P<giorno_fine>\d{1,2})[./]"
        r"(?P<mese_fine>\d{1,2})\s*[./]\s*(?P<anno>\d{4})",
        # prova dal vivo 06/10 (documenti dai feed delle piattaforme IR): copertina del 10-Q e dei
        # bilanci intermedi IFRS («for the three and six-month periods ended June 30, 2026»)
        rf"(?P<mesi>quarter)ly\s+period\s+ended\s+(?P<fine>{_DATA_EN_SITO})",
        rf"(?P<mesi>three)\s+and\s+(?:six|nine)[\s-]+months?\s+periods?\s+ended\s+(?P<fine>{_DATA_EN_SITO})",
        rf"(?P<mesi>three)[\s-]+months?\s+(?:periods?\s+)?ended\s+(?P<fine>{_DATA_EN_SITO})",
    ],
}


def _periodo_sito(testo, tipo, url, altri):
    """Prima regola di _PERIODI_SITO che regge sul documento e sugli altri; se nessuna regge su
    tutti, la prima che regge sul documento (gli altri restano da verificare nel run, dichiarati)."""
    from bellomberg.market_data import filing_proposta_ai as fp
    validi = [rx for rx in _PERIODI_SITO.get(tipo, []) if fp._periodo_regge(testo, rx, tipo, url)]
    tutti = [rx for rx in validi if all(fp._periodo_regge(t, rx, tipo, u) for u, t in altri)]
    return (tutti or validi or [None])[0]


def _senza_fonte(ticker, proposta, motivo):
    return {"ticker": ticker, "esito": "senza_fonte", "proposta": proposta, "motivo": motivo}


MAX_TENTATIVI_SITO = 2  # relazioni scelte provate per titolo (ognuna: fino a 2 PDF da 40 MB)
# Revisione R-8 C1: parole che, subito DOPO il nome dell'emittente, indicano un'ALTRA entita' del
# gruppo (veicolo, fondo pensione, controllata con altra forma giuridica). Quelle gia' nel nome
# dell'emittente non contano («Zztest Holding AG»: «holding» e «ag» sono suoi).
_QUALIFICHE_ENTITA = (
    "finance", "financing", "funding", "capital", "treasury", "pension", "pensions", "pensionskasse",
    "insurance", "reinsurance", "leasing", "bank", "beteiligungs", "beteiligung", "holding", "holdings",
    "international", "services", "trust", "foundation", "stiftung",
    "bv", "b.v.", "gmbh", "ltd", "limited", "inc", "llc", "sarl", "s.a.r.l.", "srl", "s.r.l.", "ag", "se",
    "sa", "s.a.", "spa", "s.p.a.", "plc", "nv", "n.v.", "ab", "asa", "oyj", "kgaa", "kg")
_PAROLE_GRUPPO = ("group", "gruppo", "groupe", "grupo", "groep")


def _parole_del_nome(nome):
    import re
    return {w.replace(".", "") for w in re.findall(r"[a-z.]+", str(nome or "").lower())}


def _qualifiche_estranee(nome):
    import re
    proprie = _parole_del_nome(nome)
    return "|".join(re.escape(q) for q in _QUALIFICHE_ENTITA if q.replace(".", "") not in proprie)


def regex_soggetto(nome):
    """Prova dell'emittente come SOGGETTO: il nome non seguito da una qualifica di un'altra entita'
    («Zztest Group» si', «Zztest Group Finance BV» no). Usata anche nel profilo (run)."""
    from bellomberg.market_data.filing_profili_auto import _regex_nome
    return _regex_nome(nome) + rf"(?![\s,.\-–]*(?:{_qualifiche_estranee(nome)})(?![a-z]))"


def regex_perimetro(nome):
    """Prova del perimetro consolidato che NON si regge sul nome dell'emittente (R-8 C1)."""
    proprie = _parole_del_nome(nome)
    gruppo = [w for w in _PAROLE_GRUPPO if w not in proprie]
    return "consolidat|konzern" + (rf"|\b(?:{'|'.join(gruppo)})\b" if gruppo else "")


def _altra_entita(testo, nome):
    """Il nome dell'entita' se la PRIMA citazione del nome in copertina e' un'altra entita' del
    gruppo, None se il documento e' dell'emittente."""
    import re
    from bellomberg.market_data.filing_profili_auto import _regex_nome
    testa = testo[:3000]
    m = re.search(_regex_nome(nome), testa, re.I)
    if not m:
        return None
    coda = re.match(rf"(?:[\s,.\-–]*(?:{_qualifiche_estranee(nome)})(?![a-z]))+", testa[m.end():], re.I)
    return " ".join(testa[m.start():m.end() + coda.end()].split()) if coda else None


def _codici_discordi(testo, lei=None, isin=None):
    """Motivo se il PDF dichiara LEI/ISIN e nessuno e' quello dell'emittente, None altrimenti."""
    import re
    for etichetta, atteso, forma in (("LEI", lei, r"[A-Z0-9]{18}[0-9]{2}"), ("ISIN", isin, r"[A-Z]{2}[A-Z0-9]{9}[0-9]")):
        if not atteso:
            continue
        trovati = set(re.findall(rf"\b{etichetta}\b\W{{0,12}}({forma})\b", testo[:200_000]))
        if trovati and str(atteso).upper() not in trovati:
            return (f"{etichetta} nel documento ({', '.join(sorted(trovati)[:3])}) diverso da quello dell'emittente "
                    f"({str(atteso).upper()}): documento di un altro soggetto")
    return None


# ------------------------------------------------------------------ alias dichiarati (main 07/10)
# «Piu' aperti, ma mai documenti di un'altra societa'»: l'identita' accetta gli ALIAS DICHIARATI
# dell'emittente (nome del titolo, nome ESEF, GLEIF legalName/otherNames del SUO LEI, Yahoo
# longName/shortName del SUO ticker), in forma normalizzata (forma giuridica, apostrofi, accenti,
# maiuscole). Il match dichiara quale alias ha retto. Controllate con qualifiche e LEI/ISIN diversi
# restano rifiutati.
_APOSTROFI = "'\u2019\u2018\u02bc`\u00b4"
_LETTERE_ACCENTATE = {"a": "a\u00e0\u00e1\u00e2\u00e3\u00e4\u00e5", "e": "e\u00e8\u00e9\u00ea\u00eb",
                      "i": "i\u00ec\u00ed\u00ee\u00ef", "o": "o\u00f2\u00f3\u00f4\u00f5\u00f6\u00f8",
                      "u": "u\u00f9\u00fa\u00fb\u00fc", "n": "n\u00f1", "c": "c\u00e7", "y": "y\u00fd\u00ff"}
_ALIAS_GENERICI = {"group", "gruppo", "groupe", "grupo", "holding", "holdings", "bank", "banca", "company",
                   "international", "the", "investor", "investors", "relations"}
MIN_ALIAS = 4  # lettere minime di un alias normalizzato
_TIPI_NOMI_GLEIF = {"TRADING_OR_OPERATING_NAME", "ALTERNATIVE_LANGUAGE_LEGAL_NAME",
                    "AUTO_ASCII_TRANSLITERATED_LEGAL_NAME", "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME"}


def norm_alias(nome):
    """«Z’Oréal S.A.» -> «zoreal»: apostrofi tolti, accenti via, minuscole, senza forma giuridica."""
    import re
    import unicodedata
    from bellomberg.market_data.filing_identita import _FORME
    t = str(nome or "")
    for a in _APOSTROFI:
        t = t.replace(a, "")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return " ".join(p for p in re.sub(r"[^a-z0-9]+", " ", t).split() if p not in _FORME)


def regex_alias(nome):
    """Regex sul testo GREZZO del documento per un alias: lettere con le varianti accentate, apostrofo
    facoltativo fra le lettere, separatori del nome fra le parole. None se l'alias e' vuoto."""
    import re
    from bellomberg.market_data.filing_profili_auto import _SEP_NOME
    parole = norm_alias(nome).split()
    if not parole:
        return None
    apo = "[" + _APOSTROFI + "]?"

    def lettera(c):
        varianti = _LETTERE_ACCENTATE.get(c)
        return f"[{varianti}]" if varianti else re.escape(c)
    return r"\b" + _SEP_NOME.join(apo.join(lettera(c) for c in p) for p in parole) + r"\b"


def _proprie(alias):
    """Parole (forme giuridiche comprese) di tutti gli alias: non sono qualifiche di un'altra entita'."""
    out = set()
    for a in alias:
        out |= _parole_del_nome(a["nome"])
    return out


def _qualifiche_da_parole(proprie):
    import re
    return "|".join(re.escape(q) for q in _QUALIFICHE_ENTITA if q.replace(".", "") not in proprie)


def regex_soggetto_alias(nome, proprie):
    rx = regex_alias(nome)
    return rx and rx + rf"(?![\s,.\-–]*(?:{_qualifiche_da_parole(proprie)})(?![a-z]))"


def _altra_entita_alias(testo, alias, proprie):
    """Nome dell'entita' se la PRIMA citazione di un alias in copertina e' seguita da una qualifica di
    un'altra entita' del gruppo («ZZITEX Finance BV»), None altrimenti."""
    import re
    testa = testo[:3000]
    for a in alias:
        rx = regex_alias(a["nome"])
        m = re.search(rx, testa, re.I) if rx else None
        if not m:
            continue
        coda = re.match(rf"(?:[\s,.\-–]*(?:{_qualifiche_da_parole(proprie)})(?![a-z]))+", testa[m.end():], re.I)
        if coda:
            return " ".join(testa[m.start():m.end() + coda.end()].split())
    return None


def alias_emittente(ticker, nome, *, lei=None, proposta=None):
    """([{"nome", "fonte"}], avvisi): alias dichiarati, nell'ordine nome del titolo, ESEF, GLEIF (solo il
    record col LEI ESATTO), Yahoo (il ticker). Doppioni normalizzati tolti; alias troppo corti o generici
    scartati e dichiarati; una fonte in errore e' dichiarata negli avvisi."""
    from bellomberg.market_data import esef_sito
    grezzi, avvisi = [(nome, "nome del titolo")], []
    lei = str(lei or "").upper() or None
    esef = (proposta or {}).get("esef") if isinstance(proposta, dict) else None
    for c in (esef or {}).get("candidati") or []:
        # revisione R-FONTI: senza LEI nessun alias dai candidati ESEF (potrebbero essere altre societa')
        if isinstance(c, dict) and c.get("nome") and lei and str(c.get("lei") or "").upper() == lei:
            grezzi.append((c["nome"], "ESEF (filings.xbrl.org)"))
    if lei:
        try:
            for r in esef_sito.gleif_lei_records(lei) or []:
                if not isinstance(r, dict):
                    continue
                entita = ((r.get("attributes") or {}).get("entity") or {})
                successori = {str((x or {}).get("lei") or "").upper() for x in entita.get("successorEntities") or []
                              if isinstance(x, dict)}
                stato = str(((r.get("attributes") or {}).get("registration") or {}).get("status") or "").upper()
                if str(r.get("id") or "").upper() == lei:
                    nota = ""
                elif lei in successori and stato == "DUPLICATE":
                    # solo il DUPLICATO dello stesso emittente: una controllata FUSA ha lo stesso successore
                    # ma e' un'altra societa' (prova dal vivo 07/10), mai un alias
                    # prova dal vivo 07/10: il record DUPLICATE dello stesso emittente porta il nome breve
                    nota = " (record duplicato con successore il LEI dell'emittente)"
                else:
                    continue  # mai il record di un altro LEI
                grezzi.append(((entita.get("legalName") or {}).get("name"), "GLEIF legalName" + nota))
                for chiave, fonte in (("otherNames", "GLEIF otherNames"),
                                      ("transliteratedOtherNames", "GLEIF transliteratedOtherNames")):
                    for n in entita.get(chiave) or []:
                        if not isinstance(n, dict):
                            continue
                        tipo = str(n.get("type") or "").upper()
                        # revisione R-FONTI S3: il nome legale PRECEDENTE puo' essere quello di una societa'
                        # poi scissa e la data del cambio non e' nota: escluso; tipi ignoti esclusi
                        if tipo == "PREVIOUS_LEGAL_NAME":
                            avvisi.append(f"alias «{n.get('name')}» (GLEIF): nome legale precedente, escluso "
                                          "(data del cambio non nota)")
                            continue
                        if tipo and tipo not in _TIPI_NOMI_GLEIF:
                            avvisi.append(f"alias «{n.get('name')}» (GLEIF, tipo {tipo}): escluso")
                            continue
                        grezzi.append((n.get("name"), fonte + nota))
        except Exception as exc:
            avvisi.append(f"alias GLEIF non letti ({esef_sito.motivo_eccezione(exc, 80)})")
    try:
        info = esef_sito._info_yfinance(ticker) or {}
        grezzi += [(info.get("longName"), "Yahoo longName"), (info.get("shortName"), "Yahoo shortName")]
    except Exception as exc:
        avvisi.append(f"alias Yahoo non letti ({esef_sito.motivo_eccezione(exc, 80)})")
    import re
    for grezzo, fonte in list(grezzi):
        # «… S.A. (INDITEX, S.A.)»: il nome tra parentesi e' un alias dichiarato dalla stessa fonte
        for dentro in re.findall(r"\(([^()]{2,80})\)", str(grezzo or "")):
            grezzi.append((dentro.strip(), fonte.rstrip(")") + ", tra parentesi)" if fonte.endswith(")")
                           else fonte + " (tra parentesi)"))
    alias, visti = [], set()
    for grezzo, fonte in grezzi:
        if ";" in str(grezzo or ""):  # «MARCA ; MARCA ; …» (otherNames GLEIF): marchi, non il nome dell'emittente
            avvisi.append(f"alias «{str(grezzo)[:60]}» ({fonte}): elenco di marchi, scartato")
            continue
        n = norm_alias(grezzo)
        if not grezzo or not n or n in visti:
            continue
        if len(n.replace(" ", "")) < MIN_ALIAS or set(n.split()) <= _ALIAS_GENERICI:
            avvisi.append(f"alias «{grezzo}» ({fonte}) troppo generico: scartato")
            continue
        visti.add(n)
        alias.append({"nome": str(grezzo), "fonte": fonte})
    return alias, avvisi


def _alias_che_regge(testo, alias, proprie):
    """(alias, regex) del primo alias che prova l'emittente come SOGGETTO nelle prime battute."""
    from bellomberg.market_data import filing_proposta_ai as fp
    for a in alias:
        rx = regex_soggetto_alias(a["nome"], proprie)
        if rx and fp._prova(rx, testo[:20_000]):
            return a, rx
    return None, None


# ------------------------------------------------------------------ soggetto in copertina (revisione R-FONTI)
# Un alias presente «da qualche parte» non prova che il documento sia DELL'emittente: una capogruppo cita la
# controllata, un broker la societa' che copre, una societa' scissa porta l'ex nome come prefisso. In
# copertina (prime COPERTINA battute) l'emittente deve comparire, e nessuna altra entita' con forma giuridica
# deve precederlo (revisori esclusi). Un'entita' il cui nome FINISCE con un alias e' l'emittente
# («… Limited Review INDUSTRIA DE DISEÑO TEXTIL, S.A.», «Financial Statements Nu Holdings Ltd.»).
COPERTINA_BATTUTE = 4000
_FORME_ENTITA = (r"(?:AG|SE|S\.\s?p\.\s?A\.?|SpA|S\.\s?A\.?|SA|N\.\s?V\.?|NV|plc|PLC|Inc\.?|INC\.?|Ltd\.?|LTD\.?"
                 r"|Ltda\.?|Limited|LIMITED|GmbH|B\.\s?V\.?|LLC|Corp\.?|CORP\.?|Corporation|CORPORATION|AB|ASA|Oyj"
                 r"|KGaA|SAS|S\.\s?A\.\s?S\.?)")
_PAROLA_MAIUSCOLA = r"[A-ZÀ-ÖØ-Þ][\w’'&.\-]*"
_CONNETTIVI = r"(?:de|di|del|della|der|des|du|of|the|and|und|et|y|e|&|von|van)"
_ENTITA = (rf"({_PAROLA_MAIUSCOLA}(?:[ \t]+(?:{_PAROLA_MAIUSCOLA}|{_CONNETTIVI}))*)[ \t]*,?[ \t]+"
           rf"({_FORME_ENTITA})(?![\w])")
# Revisione R-SITI2 (impianto 07/10, E9): revisori, banche depositarie e borse si saltano SOLO come ENTITA'
# nominate, da una lista CHIUSA di nomi guardata sul nome dell'entita'; mai per parole di contesto che seguono
# («(NYSE: …)», «listed on …», «soggetto a revisione»). Un nome fuori lista conta come altra entita'.
_NON_SOGGETTI = (r"\b(?:kpmg|deloitte|pricewaterhouse\w*|pwc|ernst|ey|mazars|forvis|grant[\s-]*thornton|bdo|crowe"
                 r"|baker[\s-]*tilly|rsm|audirevi|pkf|kreston|moore|nexia|hlb|ria[\s-]*grant[\s-]*thornton"
                 r"|bank[\s-]+of[\s-]+new[\s-]+york[\s-]+mellon|bny[\s-]*mellon|citibank|jpmorgan[\s-]*chase[\s-]*bank"
                 r"|deutsche[\s-]*bank[\s-]*trust|borsa[\s-]*italiana|euronext|deutsche[\s-]*b(?:ö|oe|o)rse|nyse"
                 r"|nasdaq|six[\s-]*swiss[\s-]*exchange|london[\s-]*stock[\s-]*exchange|bolsas[\s-]*y[\s-]*mercados)\b")
# Revisione R-SITI2 D4: un'entita' che FINISCE con un alias e' l'emittente solo se la parte prima sono parole da
# documento («Limited Review», «Financial Statements», «Agli Azionisti della»), mai una ragione sociale («Banca»)
_PAROLE_DOCUMENTO = frozenset((
    "limited review report reports interim condensed consolidated financial statements statement annual half year "
    "yearly quarterly unaudited audited results form document universal registration independent auditor auditors "
    "relazione relazioni finanziaria finanziario semestrale annuale trimestrale bilancio bilanci consolidato "
    "consolidata revisione contabile limitata rapport rapports financier semestriel annuel bericht konzernabschluss "
    "halbjahresfinanzbericht geschaftsbericht informe anual financiero cuentas american depositary shares "
    "of the on to for and de di del della dei degli delle des du der die das und et la le les il al ai agli alle "
    "azionisti shareholders stockholders members soci actionnaires aktionare accionistas sur su in en im zum zur "
    "a an by per pour fur "
    # v2 (prova reale: relazione del revisore «To the Shareholders and Board of Directors of <Registrante> Ltd.»
    # nel 6-K dell'emittente, presa per un'altra entita'): destinatari della relazione, non ragioni sociali
    "board directors supervisory consiglio amministrazione").split())
def _prefisso_da_documento(n, x):
    """L'entita' normalizzata `n` finisce con l'alias `x` e prima ha solo parole da documento."""
    if not x:
        return False
    if n == x:
        return True
    if not n.endswith(" " + x):
        return False
    return all(w in _PAROLE_DOCUMENTO for w in n[:-len(x)].split())


def _soggetto_in_copertina(testo, alias):
    """None se in copertina l'emittente e' il soggetto, altrimenti il motivo (dichiarato)."""
    import re
    copertina = testo[:COPERTINA_BATTUTE]
    norme = [norm_alias(a["nome"]) for a in alias]
    primo = None
    for a in alias:
        rx = regex_alias(a["nome"])
        m = re.search(rx, copertina, re.I) if rx else None
        if m and (primo is None or m.start() < primo):
            primo = m.start()
    if primo is None:
        return (f"soggetto non provato in copertina: nessun alias dell'emittente nelle prime {COPERTINA_BATTUTE} "
                "battute del documento")
    for m in re.finditer(_ENTITA, copertina):
        if m.start() > primo:
            break
        nome_entita = " ".join(m.group(0).split())
        n = norm_alias(nome_entita)
        if not n or re.search(_NON_SOGGETTI, nome_entita, re.I):
            continue
        if not any(_prefisso_da_documento(n, x) for x in norme):
            return f"in copertina il soggetto e' «{nome_entita}», non l'emittente: documento di un'altra entita'"
    return None


def _nota_di_ricerca(testo):
    """Motivo se la copertina e' quella di una nota di broker/analista, None altrimenti. Il giudizio e' del
    modulo note_ricerca (lessico EN/IT/DE/FR/ES, solo i disclaimer della lista chiusa lo annullano)."""
    from bellomberg.market_data.note_ricerca import motivo_nota_ricerca
    return motivo_nota_ricerca(testo[:COPERTINA_BATTUTE])


def _riscontro(riscontro_fn, testo, *, ticker, lei, isin, periodo_fine):
    """Esito del riscontro numerico di un documento da fonte esterna, mai un'eccezione: modulo assente o in
    errore = «senza_confronto» col motivo (impianto 07/10, aggiunta PM: riscontro con fonte indipendente)."""
    cik = None
    try:
        if riscontro_fn is None:
            from bellomberg.market_data.riscontro_numerico import riscontra as riscontro_fn
            try:  # CIK dalla mappa ticker->CIK del repo (sec_edgar): solo nella chiamata vera, mai nei test finti
                from bellomberg.market_data.sec_edgar import lookup_cik
                cik = lookup_cik(ticker)
            except Exception:
                cik = None  # il modulo del riscontro dichiara da solo la fonte mancante
    except ImportError:
        return {"esito": "senza_confronto", "motivo": "modulo del riscontro numerico non disponibile"}
    try:
        esito = riscontro_fn(testo, ticker=ticker, cik=cik, lei=lei, isin=isin, periodo_fine=periodo_fine)
    except Exception as exc:
        from bellomberg.market_data import esef_sito
        return {"esito": "senza_confronto", "motivo": f"riscontro non eseguito ({esef_sito.motivo_eccezione(exc, 80)})"}
    return esito if isinstance(esito, dict) and esito.get("esito") else {
        "esito": "senza_confronto", "motivo": "riscontro senza esito"}


def _fonte_esterna(url, dominio, host_redirect=()):
    """Host della FONTE ESTERNA del documento, None se e' del dominio dell'emittente o di una piattaforma IR
    della lista (impianto 07/10, main): CDN generici, sito di gruppo trovato dal link «Group», host di
    redirect fuori dominio. Un documento da fonte esterna non diventa MAI «verificato»."""
    from urllib.parse import urlsplit
    from bellomberg.market_data import esef_sito
    casa = dominio[0] if isinstance(dominio, tuple) else dominio
    for h in host_redirect or ():
        if not (casa and esef_sito.stesso_dominio(f"https://{h}/", casa)) and not esef_sito.piattaforma_documenti(h):
            return h
    host = (urlsplit(url).hostname or "").lower()
    if not dominio or esef_sito.piattaforma_documenti(host):
        return None
    if not esef_sito.stesso_dominio(url, dominio):
        return host  # CDN generico linkato dal sito
    if getattr(dominio, "gruppo", False) and not esef_sito.stesso_dominio(url, casa):
        return host  # sito di gruppo dal link «Group»
    return None


def _scarica_pdf_sito(url, cartella, *, nav=None, dominio=None, via_host=None):
    """PDF del sito: robots.txt dell'host rispettato (5xx/irraggiungibile = vietato), solo il dominio
    dell'emittente oppure l'host `via_host` da cui la sua pagina IR lo linka (decisione PM 06/10 sera:
    CDN ammessi con l'etichetta), solo quell'host, IP pubblico, tetto 40 MB."""
    from urllib.parse import urlsplit
    from bellomberg.market_data import download_sicuro, esef_sito, lettore_trimestrali
    host = (urlsplit(url).hostname or "").lower()
    if dominio and not esef_sito.stesso_dominio(url, dominio) and not (
            via_host and host == str(via_host).lower() and urlsplit(url).scheme == "https"
            and (esef_sito.piattaforma_documenti(host) or esef_sito.cdn_generico(host))):  # R-FONTI S6 + CDN
        return {"stato": "errore", "motivo": f"PDF su un altro dominio ({host}): non scaricato"}
    proprio = esef_sito.dominio_registrabile(host) or host
    if nav is None or getattr(nav, "dominio", proprio) != proprio:
        nav = esef_sito.Navigatore(proprio)  # robots.txt e ritmo dell'host del PDF
    if not nav.consentito(url):
        stato, ignoto = nav.bloccato.get(host), nav.robots_ignoto.get(host)
        return {"stato": "errore", "motivo": (f"{esef_sito.frase_blocco(stato)} su robots.txt" if stato
                                              else f"robots.txt non leggibile ({ignoto})" if ignoto
                                              else f"robots.txt vieta {urlsplit(url).path[:80]}")}
    r = lettore_trimestrali.scarica_documento(url, str(cartella), host_consentiti={host}, public_only=True,
                                              max_bytes=download_sicuro.MAX_PDF)
    if r.get("stato") == "ok" or not str(r.get("motivo") or "").startswith("ValueError: host non consentito:"):
        return r
    # Prova dal vivo 06/10: l'URL dell'emittente rimanda (redirect) al CDN dei contenuti. Ammesso
    # (decisione PM 06/10 sera) seguendo la catena a mano: robots.txt di OGNI host rispettato, blocchi
    # dichiarati; gli host del CDN tornano nel risultato («host_redirect») per il run.
    catena = _risolvi_redirect(url, dominio=dominio)
    if catena.get("motivo"):
        return {"stato": "errore", "url": url, "motivo": catena["motivo"]}
    r = lettore_trimestrali.scarica_documento(url, str(cartella), host_consentiti={host} | set(catena["host"]),
                                              public_only=True, max_bytes=download_sicuro.MAX_PDF)
    if r.get("stato") == "ok":
        r["host_redirect"] = sorted(catena["host"])
    return r


def _risolvi_redirect(url, dominio=None):
    """{"host": [host dei redirect]} oppure {"motivo"}: segue fino a esef_sito.MAX_REDIRECT redirect
    con richieste senza corpo, robots.txt dell'host di ogni salto, IP pubblici, solo https. Revisione
    R-SITI2 D3: i salti vanno SOLO verso il dominio dell'emittente, una piattaforma IR o un CDN della lista."""
    from urllib.parse import urljoin, urlsplit
    import requests
    from bellomberg.market_data import esef_sito
    from bellomberg.market_data.lettore_trimestrali import UA, _richiedi_indirizzi_pubblici
    origine = (urlsplit(url).hostname or "").lower()
    riferimento = dominio or esef_sito.dominio_registrabile(origine) or origine
    corrente, visti, navi = url, [], {}
    for _ in range(esef_sito.MAX_REDIRECT + 1):
        p = urlsplit(corrente)
        host = (p.hostname or "").lower()
        if p.scheme != "https" or not host or p.username is not None or p.password is not None:
            return {"motivo": f"redirect verso un indirizzo non https o con credenziali ({host or '?'})"}
        if corrente != url and not (esef_sito.stesso_dominio(corrente, riferimento)
                                    or esef_sito.piattaforma_documenti(host) or esef_sito.cdn_generico(host)):
            return {"motivo": f"redirect verso un host terzo ({host}): non scaricato (ammessi solo il dominio "
                              "dell'emittente, piattaforme IR e CDN della lista)"}
        dom = esef_sito.dominio_registrabile(host) or host
        nav = navi.setdefault(dom, esef_sito.Navigatore(dom))
        if not nav.consentito(corrente):
            stato, ignoto = nav.bloccato.get(host), nav.robots_ignoto.get(host)
            return {"motivo": (f"{esef_sito.frase_blocco(stato)} su robots.txt di {host}" if stato else
                               f"robots.txt di {host} non leggibile ({ignoto})" if ignoto else
                               f"robots.txt di {host} vieta {p.path[:80]}")}
        try:
            _richiedi_indirizzi_pubblici(host, p.port or 443)
            r = requests.get(corrente, timeout=esef_sito.TIMEOUT_S, headers={"User-Agent": UA},
                             allow_redirects=False, stream=True)
            r.close()
        except Exception as exc:
            return {"motivo": f"redirect non seguito ({esef_sito.motivo_eccezione(exc, 120)})"}
        if r.status_code in esef_sito.STATI_BLOCCO:
            return {"motivo": f"{esef_sito.frase_blocco(r.status_code)} su {host}"}
        if r.status_code not in (301, 302, 303, 307, 308) or not r.headers.get("Location"):
            return {"host": [h for h in dict.fromkeys(visti) if h != origine]}
        corrente = urljoin(corrente, r.headers["Location"])
        visti.append((urlsplit(corrente).hostname or "").lower())
    return {"motivo": f"oltre {esef_sito.MAX_REDIRECT} redirect: documento non scaricato"}


def profilo_dal_sito(ticker, *, nome, scelta, scarica_fn=None, dominio=None, lei=None, isin=None, alias=None,
                     riscontro_fn=None):
    """{"profilo", "periodo", "avvisi"} se il PDF scelto sul sito e' dell'emittente, altrimenti {"motivo"}.
    Il profilo dice se e' verificato (`verificato`, `controlli_non_superati`, `periodo_stato`): fuori
    restano solo PDF non scaricabili (robots.txt, blocchi) o illeggibili e documenti di un altro soggetto.

    Regole di verifica tutte deterministiche: lingua dal testo, tipo dalle prove del codice,
    emittente come SOGGETTO del documento (mai una controllata col suo nome; LEI/ISIN se il PDF li
    dichiara), perimetro che non si regge sul nome, periodo dalle regole del codice (nessun
    modello). Senza regex di sezione: tutto il testo come una sezione (sezioni_intero, come i 6-K)."""
    import re
    import tempfile
    from bellomberg.market_data import esef_sito
    from bellomberg.market_data import filing_proposta_ai as fp
    from bellomberg.market_data.filing_profili_auto import _LINGUA_ESEF
    from bellomberg.market_data.filing_verifica import verifica_documento
    from bellomberg.market_data.lettore_trimestrali import estrai_testo
    ultimo, precedente = scelta["ultimo"], scelta.get("precedente")
    nav = None
    if scarica_fn is None:
        from urllib.parse import urlsplit
        nav = esef_sito.Navigatore(dominio or esef_sito.dominio_registrabile(urlsplit(ultimo["url"]).hostname))
    with tempfile.TemporaryDirectory() as cartella:
        scaricati, avvisi, host_redirect, esterni = {}, [], set(), {}
        for doc in (ultimo, precedente):
            if not doc:
                continue
            try:
                if scarica_fn is not None:
                    r = scarica_fn(doc["url"], cartella)
                else:
                    if scaricati:
                        nav._dormi(esef_sito.PAUSA_S)  # pausa anche fra un PDF e l'altro
                    r = _scarica_pdf_sito(doc["url"], cartella, nav=nav, dominio=dominio, via_host=doc.get("via_host"))
            except Exception as exc:
                r = {"stato": "errore", "motivo": esef_sito.motivo_eccezione(exc)}
            if r.get("stato") != "ok":
                if doc is ultimo:
                    return {"motivo": f"PDF non scaricato ({esef_sito._URL_NEL_TESTO.sub('<url>', str(r.get('motivo')))})"}
                avvisi.append(f"PDF dell'anno prima non scaricato ({r.get('motivo')}): solo il documento recente")
                continue
            scaricati[doc["url"]] = r["path"]
            host_redirect.update(r.get("host_redirect") or [])
            # impianto 07/10: fonte esterna (CDN, sito di gruppo, redirect fuori dominio) = mai verificato
            esterno = _fonte_esterna(doc["url"], dominio, r.get("host_redirect"))
            if esterno:
                esterni[doc["url"]] = esterno
        try:
            lingua = fp.estrai_input(scaricati[ultimo["url"]])["lingua_rilevata"]
        except ValueError as exc:
            return {"motivo": f"PDF non utilizzabile: {exc}"}
        if not lingua or lingua not in _LINGUA_ESEF:
            return {"motivo": f"lingua del documento non determinata ({lingua or 'ignota'}): nessuna lingua di ripiego"}
        testi = {u: estrai_testo(p).get("testo") or "" for u, p in scaricati.items()}
        testo = testi[ultimo["url"]]
        # identita' (R-8 C1): il documento deve essere DELL'emittente, non di un'entita' col suo nome;
        # alias dichiarati (main 07/10), il primo e' sempre il nome del titolo
        alias = alias or [{"nome": nome, "fonte": "nome del titolo"}]
        proprie = _proprie(alias)
        altra = _altra_entita_alias(testo, alias, proprie)
        if altra:
            return {"motivo": f"documento di un'altra entita' del gruppo («{altra}»), non dell'emittente «{nome}»"}
        discordi = _codici_discordi(testo, lei, isin)
        if discordi:
            return {"motivo": discordi}
        ricerca = _nota_di_ricerca(testo)  # revisione R-FONTI S6
        if ricerca:
            return {"motivo": ricerca}
        for u in [u for u in testi if u != ultimo["url"]]:
            perche = (_altra_entita_alias(testi[u], alias, proprie) or _codici_discordi(testi[u], lei, isin)
                      or _nota_di_ricerca(testi[u]) or _soggetto_in_copertina(testi[u], alias))
            if perche:
                avvisi.append(f"PDF dell'anno prima scartato: documento di un altro soggetto ({perche})")
                del testi[u], scaricati[u]
        # Decisione PM 06/10 sera: oltre l'identita' (sopra e qui sotto) nessun controllo blocca il
        # profilo: chi non passa entra «non verificato» con i controlli non superati, dichiarati.
        controlli = []
        perimetro = regex_perimetro(" ".join(a["nome"] for a in alias))
        nomi_rx = "|".join(f"(?:{rx})" for rx in (regex_alias(a["nome"]) for a in alias) if rx)
        if not re.search(perimetro, re.sub(nomi_rx, " ", testo[:200_000], flags=re.I) if nomi_rx else testo[:200_000],
                         re.I):
            controlli.append("perimetro consolidato non dichiarato nel documento (il nome dell'emittente non "
                             "conta): bilancio della sola capogruppo o non riconosciuto")
        urls = [d["url"] for d in (ultimo, precedente) if d and d["url"] in scaricati]
        profilo = {"ticker": ticker, "emittente_id": f"EMITTENTE:{nome}", "nome": nome,
                   "origine_collegamento": ORIGINE_COLLEGAMENTO_SITO,
                   "origine_documenti": ultimo.get("etichetta") or esef_sito.ETICHETTA_SITO,
                   "via_host": ultimo.get("via_host"), "tipo_documento": ultimo.get("tipo_documento") or "relazione",
                   "fonti": ["ir"], "ir_urls": urls, "lingua": lingua, "tipo": ultimo["tipo"],
                   "perimetro": "consolidato",
                   "verifica": {"lingua": _LINGUA_ESEF[lingua], "perimetro": perimetro,
                                "tipo": fp._PROVA_TIPO[ultimo["tipo"]],
                                "emittente": regex_soggetto_alias(alias[0]["nome"], proprie) or regex_soggetto(nome)},
                   "sezioni": {}, "sezioni_intero": True, "periodo_regola": "piu_recente"}
        if host_redirect:  # CDN raggiunti per redirect dall'URL dell'emittente (robots.txt gia' letto)
            profilo["host_documenti"] = sorted(host_redirect)
            # revisione R-SITI2 D3: l'etichetta nomina l'host che serve davvero il PDF
            profilo["origine_documenti"] = profilo["origine_documenti"].replace(
                ", non archivio ufficiale", f", PDF servito via redirect da {', '.join(sorted(host_redirect))}, "
                                            "non archivio ufficiale", 1)
        if lei:  # per l'aggiornamento giornaliero (revisione R-SITI2: LEI/ISIN anche li')
            profilo["lei_emittente"] = str(lei).upper()
        if isin:
            profilo["isin_emittente"] = str(isin).upper()
        altri = [(u, t) for u, t in testi.items() if u != ultimo["url"]]
        regole = fp._scegli_regole(testo, profilo, {}, ultimo["url"], altri)
        if not regole["periodo"]["regex"]:
            regole["periodo"]["regex"] = _periodo_sito(testo, ultimo["tipo"], ultimo["url"], altri)
        retto, rx_retto = _alias_che_regge(testo, alias, proprie)
        if not retto:
            # identita': mai rilassata (un documento di un'altra societa' non entra)
            provati = "; ".join(f"«{a['nome']}» ({a['fonte']})" for a in alias)
            return {"motivo": f"nome dell'emittente «{nome}» non trovato nel documento (alias provati: {provati})"}
        fuori = _soggetto_in_copertina(testo, alias)  # revisione R-FONTI S1/S3: il SOGGETTO, non una citazione
        if fuori:
            return {"motivo": fuori}
        # nel run vale l'alias che ha retto, piu' quelli che reggono sull'anno prima (tetto della regola)
        regole_nome = [rx_retto]
        for u, t in testi.items():
            altro, rx_altro = _alias_che_regge(t, alias, proprie) if u != ultimo["url"] else (None, None)
            if rx_altro and rx_altro not in regole_nome and len("|".join(regole_nome + [rx_altro])) + 10 < 2000:
                regole_nome.append(rx_altro)
        regole["emittente"]["regex"] = (regole_nome[0] if len(regole_nome) == 1
                                        else "|".join(f"(?:{r})" for r in regole_nome))
        profilo["identita"] = {"nome": retto["nome"], "fonte": retto["fonte"]}
        profilo["alias_emittente"] = alias
        for campo in ("emittente", "tipo", "periodo"):
            if not regole[campo]["regex"]:
                controlli.append({"tipo": f"il documento non si dichiara relazione {ultimo['tipo']}",
                                  "periodo": "periodo non verificabile: nessuna regola del codice trova un periodo "
                                             "univoco del tipo"}[campo])
                continue
            profilo["verifica"][campo] = regole[campo]["regex"]
        esito = verifica_documento(scaricati[ultimo["url"]], url=ultimo["url"], profilo=profilo)
        meta = (esito.get("documento") or {}).get("metadati") if esito.get("stato") == "ok" else None
        if meta is None:
            controlli.append("PDF non verificato: " + "; ".join(esito.get("motivi") or ["verifica non riuscita"])[:300])
        visti = set(ultimo.get("periodi_visti") or ([ultimo["periodo"]] if ultimo.get("periodo") else []))
        if meta and meta["periodo_fine"] != ultimo["periodo"]:
            # prova reale 06/10: la regola del codice aveva preso il comparativo dell'anno prima nel
            # testo della semestrale nuova. Discordanza = profilo NON verificato, le due date dichiarate.
            presunto = "presunta" in str(ultimo.get("base_periodo") or "")
            controlli.append((f"periodo ambiguo: il nome indica {ultimo['periodo']} (presunto), il testo "
                              f"{meta['periodo_fine']}: da confermare") if presunto else
                             (f"periodo trovato nel testo ({meta['periodo_fine']}) diverso da quello del nome e del "
                              f"titolo ({ultimo['periodo']}): possibile comparativo, da confermare"))
        if meta:
            visti.add(meta["periodo_fine"])
        # Impianto 07/10 + aggiunta PM: un documento da fonte esterna resta «non verificato» SALVO riscontro
        # numerico con una fonte indipendente legata all'emittente (mai i numeri del documento stesso).
        # «riscontrato» = verificato per riscontro (sha256 dei byte salvato: il run lo riconosce solo se i
        # byte sono gli stessi); «diverso» o «senza_confronto» = non verificato col motivo.
        import hashlib
        from pathlib import Path
        riscontrati = {}
        for u, host in esterni.items():
            if u not in scaricati:
                continue
            fine = (meta or {}).get("periodo_fine") if u == ultimo["url"] else None
            fine = fine or next((d.get("periodo") for d in (ultimo, precedente) if d and d["url"] == u), None)
            r = _riscontro(riscontro_fn, testi.get(u, ""), ticker=ticker, lei=lei, isin=isin, periodo_fine=fine)
            if r["esito"] == "riscontrato":
                riscontrati[u] = {"sha256": hashlib.sha256(Path(scaricati[u]).read_bytes()).hexdigest(),
                                  "etichetta": r.get("etichetta") or "verificato per riscontro numerico"}
                avvisi.append(f"fonte esterna ({host}): {riscontrati[u]['etichetta']}")
            elif r["esito"] == "diverso":
                controlli.append(f"fonte esterna ({host}): numeri diversi dalla fonte indipendente "
                                 f"({r.get('motivo') or 'riscontro negativo'})")
            else:
                controlli.append(f"fonte esterna ({host}), identita' non provabile: documento mai verificato "
                                 f"({r.get('motivo') or 'nessuna fonte di confronto'})")
        if riscontrati:
            profilo["documenti_riscontrati"] = riscontrati
        if esterni:  # DOPO la verifica dell'attivazione: nel run la fonte esterna non diventa verificata
            profilo["documenti_esterni"] = {u: h for u, h in esterni.items() if u in scaricati}
        profilo["verificato"] = not controlli
        profilo["controlli_non_superati"] = controlli
        profilo["periodo_stato"] = "certo" if (meta and not controlli) else "da_confermare"
        profilo["periodi_visti"] = sorted(visti)
        # periodo per l'aggiornamento (mai verso un periodo piu' vecchio): quello verificato sul testo,
        # altrimenti quello del nome (dichiarato da confermare)
        profilo["periodo_sito"] = meta["periodo_fine"] if (meta and not controlli) else ultimo["periodo"]
    return {"profilo": profilo, "periodo": profilo["periodo_sito"], "avvisi": avvisi}


def _trovato_con_accesso(ticker, trovato, scopri_fn=None):
    """Voce del sito con l'esito d'accesso: le voci di cache scritte prima del 05/10 non lo hanno
    (R-8 C8) e si rileggono, mai lette come «nessun PDF»."""
    from bellomberg.market_data import esef_sito
    if trovato is not None and "accesso" in trovato:
        return trovato, None
    try:
        trovato = (scopri_fn or esef_sito.scopri)(ticker, forza=True)
    except Exception as exc:
        return None, f"sito dell'emittente: {esef_sito.motivo_eccezione(exc, 120)}"
    if "accesso" not in trovato:
        return None, ("sito dell'emittente: voce di cache senza esito d'accesso (scritta prima del 05/10), "
                      "riletta al prossimo giro")
    return trovato, None


def _prova_scelte(ticker, nome, trovato, *, dopo=None, lei=None, isin=None, scarica_fn=None, alias=None,
                  proposta=None, riscontro_fn=None):
    """Prima relazione del sito che si verifica, fino a MAX_TENTATIVI_SITO: una scelta che non regge
    (es. controllata col nome dell'emittente) si scarta col motivo e si prova la successiva.
    (esito di profilo_dal_sito | None, scelta, motivi degli scarti)."""
    from bellomberg.market_data import esef_sito
    # sito della societa' e, se scoperto su un altro dominio, sito IR (seguito main 06/10)
    dominio = esef_sito.domini_voce(trovato) or "dominio-ignoto.invalid"
    pdf, motivi, prima = list(trovato.get("pdf") or []), [], None
    avvisi_alias = []
    if alias is None:  # alias dichiarati dell'emittente (main 07/10): fonti in errore dichiarate
        alias, avvisi_alias = alias_emittente(ticker, nome, lei=lei, proposta=proposta)
    for _ in range(MAX_TENTATIVI_SITO):
        scelta = esef_sito.scegli_pdf(pdf, prime_pagine=trovato.get("prime_pagine"), dominio=dominio, dopo=dopo)
        if not scelta:
            break
        prima = prima or scelta
        esito = profilo_dal_sito(ticker, nome=nome, scelta=scelta, scarica_fn=scarica_fn, dominio=dominio, alias=alias,
                                 riscontro_fn=riscontro_fn,
                                 lei=lei, isin=isin)
        if "profilo" in esito:
            esito["avvisi"] = esito.get("avvisi", []) + avvisi_alias
            if trovato.get("sito_ir"):  # sito IR scoperto: dichiarato nel profilo (l'identita' e' sul documento)
                esito["profilo"].update(sito_ir=trovato["sito_ir"], sito_ir_origine=trovato.get("sito_ir_origine"))
            return esito, scelta, motivi
        motivi.append(f"{esef_sito.nome_documento(scelta['ultimo'])} al {scelta['ultimo']['periodo']} dal "
                      f"{scelta['etichetta']}: {esito['motivo']}")
        via = {d["url"] for d in (scelta["ultimo"], scelta.get("precedente")) if d}
        pdf = [v for v in pdf if v.get("url") not in via]
    return None, prima, motivi


def _attiva_sito(store, ticker, nome, proposta, motivo_base, *, trovato=None, scopri_fn=None, scarica_fn=None,
                 lei=None, isin=None, riscontro_fn=None):
    """Ultimo passo prima di «senza fonte»: relazioni in PDF dal sito dell'emittente. Blocchi del
    sito (HTTP 403, robots.txt), PDF non ammessi o non verificati restano DICHIARATI nel motivo."""
    from bellomberg.market_data import esef_sito
    if trovato is None and esef_sito.consigliere_in_corso():
        return _senza_fonte(ticker, proposta, f"{motivo_base}; sito dell'emittente non esplorato: run del "
                                              "Consigliere in corso (si riprova al controllo giornaliero)")
    if trovato is None:
        try:
            trovato = (scopri_fn or esef_sito.scopri)(ticker)
        except Exception as exc:
            return _senza_fonte(ticker, proposta, f"{motivo_base}; sito dell'emittente: "
                                                  f"{esef_sito.motivo_eccezione(exc, 120)}")
    trovato, perche = _trovato_con_accesso(ticker, trovato, scopri_fn)
    if perche:
        return _senza_fonte(ticker, proposta, f"{motivo_base}; {perche}")
    accesso = trovato.get("accesso") or {}
    if accesso.get("stato") != "ok":
        return _senza_fonte(ticker, proposta, f"{motivo_base}; sito dell'emittente: {accesso.get('motivo')}")
    dominio = esef_sito.domini_voce(trovato)
    if not esef_sito.scegli_pdf(trovato.get("pdf"), prime_pagine=trovato.get("prime_pagine"), dominio=dominio):
        perche = (esef_sito.riepilogo_scarti(trovato["pdf"], prime_pagine=trovato.get("prime_pagine"), dominio=dominio)
                  if trovato.get("pdf") else "nessun PDF di relazioni periodiche sul sito dell'emittente")
        return _senza_fonte(ticker, proposta, f"{motivo_base}; {perche}")
    if not nome:
        return _senza_fonte(ticker, proposta, f"{motivo_base}; PDF trovati sul sito dell'emittente ma nome "
                                              "dell'emittente non noto: prova d'identita' impossibile, nessun profilo")
    isin = isin or (proposta or {}).get("isin")
    esito, _, scarti = _prova_scelte(ticker, nome, trovato, lei=lei, isin=isin, scarica_fn=scarica_fn,
                                     proposta=proposta, riscontro_fn=riscontro_fn)
    if esito is None:
        return _senza_fonte(ticker, proposta, f"{motivo_base}; " + "; ".join(scarti))
    salvato = store.set_profile(ticker, esito["profilo"], enabled=True, interval_hours=INTERVALLO_AUTO_ORE)
    p = esito["profilo"]
    return {"ticker": ticker, "esito": "attivato", "fonte": ORIGINE_COLLEGAMENTO_SITO,
            "origine": p["origine_documenti"], "profilo_versione": salvato["version"], "proposta": proposta,
            "documenti": p["ir_urls"], "avvisi": esito["avvisi"] + scarti,
            "verificato": p["verificato"], "controlli_non_superati": p["controlli_non_superati"],
            "tipo_documento": p["tipo_documento"], "periodo_stato": p["periodo_stato"], "via_host": p["via_host"],
            "alias_identita": p.get("identita"),
            "motivo": f"{_frase_profilo_sito(esito)}; {motivo_base}"
                      + (f"; scartate prima: {'; '.join(scarti)}" if scarti else "")}


def _frase_profilo_sito(esito):
    """«relazione annuale al … dal sito dell'emittente … (verificata sul testo)» oppure «(NON verificata: …)»."""
    from bellomberg.market_data import esef_sito
    p = esito["profilo"]
    stato = ("verificata sul testo" if p["verificato"] else
             "NON verificata: " + "; ".join(p["controlli_non_superati"]))
    return f"{esef_sito.nome_documento(p)} al {esito['periodo']} dal {p['origine_documenti']} ({stato})"


def aggiorna_dal_sito(store, ticker, *, scopri_fn=None, scarica_fn=None, riscontro_fn=None):
    """Controllo giornaliero dei profili «sito dell'emittente»: se sul sito c'e' una relazione con
    periodo PIU' RECENTE di quella del profilo (es. la semestrale dopo l'annuale), nuova versione del
    profilo, verificata come all'attivazione. Mai verso un periodo piu' vecchio (R-8 C3); un documento
    sparito dal sito resta quello archiviato, dichiarato. Esito: invariato | aggiornato | errore."""
    from bellomberg.market_data import esef_sito
    riga = store.get_profile(ticker)
    profilo = (riga or {}).get("profile") or {}
    if profilo.get("origine_collegamento") != ORIGINE_COLLEGAMENTO_SITO:
        return {"ticker": ticker, "esito": "non_applicabile", "motivo": "profilo non creato dal sito dell'emittente"}
    trovato, perche = _trovato_con_accesso(ticker, (scopri_fn or esef_sito.scopri)(ticker), scopri_fn)
    if perche:
        return {"ticker": ticker, "esito": "errore", "motivo": perche}
    accesso = trovato.get("accesso") or {}
    if accesso.get("stato") != "ok":
        return {"ticker": ticker, "esito": "errore", "motivo": f"sito dell'emittente: {accesso.get('motivo')}"}
    attuale = profilo.get("periodo_sito")
    if not attuale:
        doc = esef_sito.classifica_pdf({"url": (profilo.get("ir_urls") or [""])[0], "testo": ""})
        attuale = doc.get("periodo")
    if not attuale:
        return {"ticker": ticker, "esito": "errore",
                "motivo": "periodo del documento del profilo non noto: nessun confronto, profilo invariato"}
    esito, scelta, scarti = _prova_scelte(ticker, profilo.get("nome"), trovato, dopo=attuale, scarica_fn=scarica_fn,
                                          alias=profilo.get("alias_emittente"), lei=profilo.get("lei_emittente"),
                                          isin=profilo.get("isin_emittente"), riscontro_fn=riscontro_fn)
    if esito is None and scelta is None:
        sul_sito = {v.get("url") for v in trovato.get("pdf") or []}
        if (profilo.get("ir_urls") or [None])[0] not in sul_sito:
            return {"ticker": ticker, "esito": "invariato",
                    "motivo": f"il documento del profilo ({attuale}) non e' piu' sul sito: resta quello archiviato; "
                              "nessuna relazione piu' recente"}
        return {"ticker": ticker, "esito": "invariato", "motivo": f"nessuna relazione piu' recente di {attuale} sul sito"}
    if esito is None:
        return {"ticker": ticker, "esito": "errore", "motivo": "nuova relazione non verificata: " + "; ".join(scarti)}
    salvato = store.set_profile(ticker, esito["profilo"], enabled=riga["enabled"],
                                interval_hours=riga["interval_hours"], qualitative_enabled=riga["qualitative_enabled"])
    p = esito["profilo"]
    return {"ticker": ticker, "esito": "aggiornato", "profilo_versione": salvato["version"],
            "verificato": p["verificato"], "controlli_non_superati": p["controlli_non_superati"],
            "tipo_documento": p["tipo_documento"], "periodo_stato": p["periodo_stato"],
            "motivo": _frase_profilo_sito(esito) + (f"; scartate prima: {'; '.join(scarti)}" if scarti else "")}


def senza_rifiutati(proposta, cik_rifiutati, lei_rifiutati):
    """Proposta senza i candidati rifiutati, anche se `proponi_fn` non li ha filtrati."""
    out = dict(proposta)
    for chiave, campo, via in (("sec", "cik", cik_rifiutati), ("esef", "lei", lei_rifiutati)):
        p = out.get(chiave)
        if not isinstance(p, dict) or not p.get("candidati"):
            continue
        restanti = [c for c in p["candidati"] if str(c.get(campo) or "").upper() not in via]
        if len(restanti) == len(p["candidati"]):
            continue
        out[chiave] = ({**p, "candidati": restanti} if restanti else
                       {**p, "stato": "nessuno", "candidati": [], "motivo": "collegamento rifiutato dall'utente"})
    return out


def attiva(store, ticker, *, cik=None, lei=None, proponi_fn=proponi, catalogo_fn=_catalogo_default,
           pref_path=None, indice_fn=None, salta_scollegati=False):
    """Crea il profilo di `ticker`: SEC se univoco o scelto (`cik`), altrimenti ESEF se il LEI
    e' univoco o scelto (`lei`). SEC in errore: errore (mai un ripiego ESEF alla cieca).

    CIK e LEI rifiutati (preferenze) non si collegano mai. Un titolo «scollegato» (profilo
    disattivato da «Scollega») si riattiva solo con un'attivazione singola; in blocco
    (`salta_scollegati`) si salta."""
    if cik is not None and lei is not None:
        raise ValueError("indicare un CIK oppure un LEI, non entrambi")
    riga = store.get_profile(ticker)
    if riga is not None:
        try:
            pref = filing_preferenze.carica(pref_path)
        except ValueError:
            pref = None  # preferenze illeggibili: mai sovrascrivere un profilo
        if pref is None or not filing_preferenze.e_scollegato(pref, ticker, riga["version"]):
            # Mai sovrascrivere un profilo esistente (anche scritto a mano) col modello standard.
            return {"ticker": ticker, "esito": "gia_attivo", "motivo": "profilo gia' presente"}
        if salta_scollegati:
            return {"ticker": ticker, "esito": "scollegato",
                    "motivo": "collegamento annullato: si ricollega solo dalla pagina Filing"}
    esito = _attiva(store, ticker, cik=cik, lei=lei, proponi_fn=proponi_fn, catalogo_fn=catalogo_fn,
                    pref_path=pref_path, indice_fn=indice_fn)
    if riga is not None and esito.get("esito") == "attivato":
        filing_preferenze.annulla_scollegato(ticker, pref_path)
    return esito


def _attiva(store, ticker, *, cik, lei, proponi_fn, catalogo_fn, pref_path, indice_fn):
    pref = filing_preferenze.carica(pref_path)
    if ticker in pref["esclusi"]:
        return {"ticker": ticker, "esito": "escluso", "motivo": "escluso dal controllo"}
    cik_rifiutati = frozenset(pref["rifiutati"].get(ticker, []))
    lei_rifiutati = filing_preferenze.rifiutati_lei(pref, ticker)
    # `rifiutati` filtra CIK e LEI (proponi_esef), cosi' anche le firme che non conoscono rifiutati_lei.
    rifiutati = cik_rifiutati | lei_rifiutati
    proposta = senza_rifiutati(proponi_fn(ticker, rifiutati=rifiutati), cik_rifiutati, lei_rifiutati | cik_rifiutati)
    sec = proposta["sec"]
    esef_p = proposta.get("esef") or {"stato": "nessuno", "candidati": [], "motivo": "ESEF non interrogato"}
    if lei is not None:
        scelto = next((c for c in esef_p["candidati"] if c["lei"] == str(lei).upper()), None)
        if scelto is None:
            return {"ticker": ticker, "esito": "errore", "proposta": proposta,
                    "motivo": "LEI scelto non tra i candidati proposti"}
        return _attiva_esef(store, ticker, scelto, "confermato_utente", proposta.get("nome"), proposta,
                            "LEI confermato dall'utente", indice_fn)
    if sec["stato"] == "errore":
        return {"ticker": ticker, "esito": "errore", "proposta": proposta, "motivo": sec["motivo"]}
    if sec["stato"] == "non_configurata" and (cik is not None or "esef" not in proposta):
        # CIK scelto o titolo USA (ESEF mai interrogato): senza contatto non c'e' nulla da provare.
        return {"ticker": ticker, "esito": "errore", "proposta": proposta, "motivo": sec["motivo"]}
    if cik is not None:
        scelto = next((c for c in sec["candidati"] if c["cik"] == str(cik).zfill(10)), None)
        if scelto is None:
            return {"ticker": ticker, "esito": "errore", "proposta": proposta,
                    "motivo": "CIK scelto non tra i candidati proposti"}
        origine = "confermato_utente"
    elif sec["stato"] == "univoco":
        scelto, origine = sec["candidati"][0], sec["candidati"][0]["origine"]
    elif (sec["stato"] == "ambiguo" and filing_identita.preferita({**proposta, "esef": esef_p}) == "esef"
          and esef_p["stato"] != "univoco"):
        return {"ticker": ticker, "esito": "da_confermare", "proposta": proposta, "motivo": "ESEF: " + esef_p["motivo"]}
    elif sec["stato"] == "ambiguo" and filing_identita.preferita({**proposta, "esef": esef_p}) == "esef":
        c = esef_p["candidati"][0]  # SEC solo per nomi simili: non e' un collegamento
        return _attiva_esef(store, ticker, c, c["origine"], proposta.get("nome"), proposta,
                            f"{esef_p['motivo']} (SEC solo per nomi simili, non collegata)", indice_fn)
    elif sec["stato"] == "ambiguo":
        return {"ticker": ticker, "esito": "da_confermare", "proposta": proposta, "motivo": sec["motivo"]}
    elif esef_p["stato"] == "univoco" and esef_p["candidati"]:
        c = esef_p["candidati"][0]
        motivo_esef = esef_p["motivo"] + (f" ({sec['motivo']}: SEC non consultata)"
                                          if sec["stato"] == "non_configurata" else "")
        return _attiva_esef(store, ticker, c, c["origine"], proposta.get("nome"), proposta, motivo_esef, indice_fn)
    elif esef_p["stato"] == "ambiguo":
        return {"ticker": ticker, "esito": "da_confermare", "proposta": proposta, "motivo": "ESEF: " + esef_p["motivo"]}
    elif sec["stato"] == "non_configurata":
        # SEC mai consultata: «senza fonte» sarebbe falso; e' un errore di configurazione dichiarato.
        return {"ticker": ticker, "esito": "errore", "proposta": proposta,
                "motivo": f"{sec['motivo']}; ESEF: {esef_p['motivo']}"}
    elif esef_p["stato"] == "errore":
        return {"ticker": ticker, "esito": "errore", "proposta": proposta, "motivo": "ESEF: " + esef_p["motivo"]}
    elif "." not in ticker:  # titolo USA: la fonte ufficiale e' la SEC, mai il sito come ripiego
        return {"ticker": ticker, "esito": "senza_fonte", "proposta": proposta,
                "motivo": f"SEC: {sec['motivo']}; ESEF: {esef_p['motivo']}"}
    else:
        return _attiva_sito(store, ticker, proposta.get("nome"), proposta,
                            f"SEC: {sec['motivo']}; ESEF: {esef_p['motivo']}")
    catalogo = catalogo_fn(ticker, cik=scelto["cik"])
    if catalogo.get("stato") == "errore":
        return {"ticker": ticker, "esito": "errore", "proposta": proposta,
                "motivo": "catalogo SEC: " + "; ".join(catalogo.get("motivi", []))[:300]}
    forme = forme_presenti(catalogo)
    nome = proposta.get("nome") or scelto["nome"]
    # Confermato dall'utente: il nome del portafoglio puo' differire da quello SEC (vale l'uno o l'altro).
    regex = regex_confermata(nome, scelto["nome"]) if origine == "confermato_utente" else None
    try:
        profilo = profilo_sec(ticker, cik=scelto["cik"], sec_ticker=scelto["ticker"],
                              nome=nome, origine=origine, forme=forme, regex_emittente=regex)
    except ValueError as exc:
        if cik is not None or "." not in ticker:  # titoli USA: mai filings.xbrl.org (come proponi)
            return {"ticker": ticker, "esito": "senza_fonte", "proposta": proposta, "motivo": str(exc)}
        # Emittente SEC senza bilanci (ADR OTC): si prova l'ESEF, come se la SEC non ci fosse.
        esef_p = proposta.get("esef") or senza_rifiutati(
            {"esef": filing_identita.proponi_esef(ticker, proposta.get("nome"), rifiutati=rifiutati)},
            cik_rifiutati, rifiutati)["esef"]
        if esef_p["stato"] == "univoco" and esef_p["candidati"]:
            c = esef_p["candidati"][0]
            return _attiva_esef(store, ticker, c, c["origine"], proposta.get("nome"), proposta,
                                f"{esef_p['motivo']} (SEC: {exc})", indice_fn)
        if esef_p["stato"] != "ambiguo":
            return _attiva_sito(store, ticker, proposta.get("nome"), {**proposta, "esef": esef_p},
                                f"SEC: {exc}; ESEF: {esef_p['motivo']}")
        return {"ticker": ticker, "esito": "da_confermare", "proposta": {**proposta, "esef": esef_p},
                "motivo": f"SEC: {exc}; ESEF: {esef_p['motivo']}"}
    salvato = store.set_profile(ticker, profilo, enabled=True, interval_hours=INTERVALLO_AUTO_ORE)
    return {"ticker": ticker, "esito": "attivato", "profilo_versione": salvato["version"], "proposta": proposta,
            "motivo": sec["motivo"], "forme": sorted(forme)}


def attiva_mancanti(store, tickers, **kw):
    """Attivazione in blocco dei titoli senza profilo: riepilogo per esito.

    Le liste restano di ticker (chiamanti e frontend invariati); `motivi` {ticker: motivo} porta
    il motivo di OGNI esito non in errore, cosi' le preferenze lo salvano (prima: null).
    `avviso_configurazione`: la frase SEC_NON_CONFIGURATA se manca il contatto SEC (l'ESEF non lo esige), misurato
    sulla causa (variabile d'ambiente), non dedotto dagli errori; altrimenti None. (Opus 5.5, 05/10)"""
    from bellomberg.market_data.sec_edgar import _contatto
    out = {k: [] for k in ("attivati", "da_confermare", "senza_fonte", "esclusi", "gia_attivi", "scollegati",
                           "errori")}
    out["motivi"] = {}
    out["avviso_configurazione"] = None if _contatto() else filing_identita.SEC_NON_CONFIGURATA
    chiave = {"attivato": "attivati", "da_confermare": "da_confermare", "senza_fonte": "senza_fonte",
              "escluso": "esclusi", "gia_attivo": "gia_attivi", "scollegato": "scollegati"}
    for ticker in dict.fromkeys(tickers):
        try:
            r = attiva(store, ticker, salta_scollegati=True, **kw)
        except Exception as exc:
            r = {"ticker": ticker, "esito": "errore", "motivo": f"{type(exc).__name__}: {exc}"}
        if r["esito"] == "errore":
            out["errori"].append({"ticker": ticker, "motivo": r["motivo"]})
        else:
            out[chiave[r["esito"]]].append(ticker)
            out["motivi"][ticker] = r.get("motivo")
    return out


def _testo_sec(url):
    import tempfile
    from bellomberg.market_data.lettore_trimestrali import estrai_testo, scarica_documento
    with tempfile.TemporaryDirectory() as tmp:
        r = scarica_documento(url, tmp, host_consentiti={"www.sec.gov", "sec.gov"}, public_only=True)
        if r.get("stato") != "ok":
            raise ValueError(r.get("motivo", "download non disponibile"))
        return estrai_testo(r["path"]).get("testo", "")


def completa_6k(store, ticker, *, allegati_fn=None, scarica_fn=None, catalogo_fn=_catalogo_default,
                oggi=None, max_6k=None):
    """Emittenti 20-F: aggiunge la variante infrannuale se i 6-K contengono relazioni.

    Cerca il documento di bilancio dei 6-K piu' recenti (stessa selezione della
    pipeline), ne deduce trimestrale/semestrale e salva una nuova versione del
    profilo. Nessuna relazione trovata: profilo invariato, esito dichiarato.
    """
    from bellomberg.market_data import filing_pipeline, sec_edgar
    allegati_fn = allegati_fn or sec_edgar.allegati_filing
    max_6k = max_6k or filing_pipeline.MAX_6K_SONDATI
    attuale = store.get_profile(ticker)
    if not attuale:
        return {"ticker": ticker, "esito": "non_applicabile", "motivo": "nessun profilo"}
    p = attuale["profile"]
    varianti = p.get("varianti") or []
    if (not p.get("cik") or len(varianti) != 1 or varianti[0].get("forme_sec") != ["20-F"]):
        return {"ticker": ticker, "esito": "non_applicabile", "motivo": "solo profili 20-F senza variante infrannuale"}
    catalogo = catalogo_fn(ticker, cik=p["cik"])
    soglia = (date.fromisoformat(oggi) if oggi else date.today()).toordinal() - filing_pipeline.GIORNI_6K
    sei_k = [d for d in catalogo.get("documenti", []) if d.get("form") == "6-K"
             and d.get("filed_date") and date.fromisoformat(d["filed_date"]).toordinal() >= soglia][:max_6k]
    errori = []
    for d in sei_k:
        try:
            documento = filing_pipeline._documento_risultati(allegati_fn(p["cik"], d["accession"]))
            if not documento:
                continue
            tipo = sonda_tipo_6k((scarica_fn or _testo_sec)(documento["url"]))
        except Exception as exc:  # un 6-K illeggibile non ferma la ricerca negli altri
            errori.append(f"{d.get('accession')}: {type(exc).__name__}: {exc}")
            continue
        if not tipo:
            continue
        nuovo = profilo_sec(ticker, cik=p["cik"], sec_ticker=p.get("sec_ticker"), nome=p.get("nome") or ticker,
                            origine=p.get("origine_collegamento", "manuale"), forme={"20-F", "6-K"}, tipo_6k=tipo,
                            regex_emittente=(p.get("verifica") or {}).get("emittente"))  # stessa prova d'identita'
        salvato = store.set_profile(ticker, nuovo, enabled=attuale["enabled"],
                                    interval_hours=attuale["interval_hours"],
                                    qualitative_enabled=attuale["qualitative_enabled"])
        return {"ticker": ticker, "esito": "aggiunto", "tipo": tipo, "profilo_versione": salvato["version"]}
    if errori:
        return {"ticker": ticker, "esito": "errore",
                "motivo": f"6-K non letti: {len(errori)} su {len(sei_k)}; " + "; ".join(errori[:3])}
    return {"ticker": ticker, "esito": "nessun_allegato",
            "motivo": f"nessuna relazione infrannuale nei {len(sei_k)} 6-K recenti"}
