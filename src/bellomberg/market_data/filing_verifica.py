"""I-20: verifica documentale tramite profili curati, riusabili fra periodi.

Il profilo non contiene date o pagine del singolo report. Le regex sono regole
di riconoscimento dell'emittente, non un certificato universale: ogni campo
richiede una prova nei byte esaminati. Nessun LLM, DB o download qui.
"""
from datetime import date
from copy import deepcopy
import hashlib
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re

from bellomberg.market_data import regex_sandbox
from bellomberg.market_data.filing_diff import prepara_documento, _norm
from bellomberg.market_data.lettore_trimestrali import estrai_testo


_DURATE = {"annuale": (350, 380), "semestrale": (170, 195),
           "trimestrale": (80, 100), "nove_mesi": (260, 285)}
_MESI = (
    ("january", "gennaio", "januar"), ("february", "febbraio", "februar"),
    ("march", "marzo", "märz"), ("april", "aprile"), ("may", "maggio", "mai"),
    ("june", "giugno", "juni"), ("july", "luglio", "juli"),
    ("august", "agosto"), ("september", "settembre"), ("october", "ottobre", "oktober"),
    ("november", "novembre"), ("december", "dicembre", "dezember"))


class _MarkupAttivo(HTMLParser):
    """Maschera commenti/script mantenendo gli offset nel markup originale.

    ix:hidden resta attivo: contiene identificatori XBRL legittimi. La
    visibilita' CSS non decide la validita' di un fatto Inline XBRL.
    """
    def __init__(self, markup):
        super().__init__()
        self.markup = markup
        self.righe = [0] + [m.end() for m in re.finditer("\n", markup)]
        self.esclusi, self.lingue, self.muto = [], [], None

    def posizione(self):
        riga, colonna = self.getpos()
        return self.righe[riga-1] + colonna

    def handle_starttag(self, tag, attrs):
        if self.muto:
            if tag in ("script", "style", "noscript", "template"):
                self.muto[2].append(tag)
            return
        if tag in ("script", "style", "noscript", "template"):
            self.muto = (tag, self.posizione(), [tag])
        elif tag == "html":
            self.lingue.extend(value for key, value in attrs if key in ("lang", "xml:lang"))

    def handle_endtag(self, tag):
        if self.muto and tag == self.muto[2][-1]:
            self.muto[2].pop()
            if self.muto[2]:
                return
            fine = self.markup.find(">", self.posizione())
            self.esclusi.append((self.muto[1], fine+1 if fine >= 0 else len(self.markup)))
            self.muto = None

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_comment(self, data):
        if not self.muto:
            fine = re.search(r"--!?>", self.markup[self.posizione():])
            self.esclusi.append((self.posizione(), self.posizione()+fine.end() if fine else len(self.markup)))

    def testo_attivo(self):
        self.feed(self.markup)
        self.close()
        if self.muto:
            self.esclusi.append((self.muto[1], len(self.markup)))
        pezzi, posizione = [], 0
        for a, b in sorted(self.esclusi):
            if a < posizione:
                continue
            pezzi.extend((self.markup[posizione:a], " "*(b-a)))
            posizione = b
        pezzi.append(self.markup[posizione:])
        return "".join(pezzi)


def _data(value):
    value = _norm(unescape(value)).lower().strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    for numero, nomi in enumerate(_MESI, 1):
        for nome in nomi:
            patterns = (rf"(\d{{1,2}})\.?\s+{nome}\s+(\d{{4}})",
                        rf"{nome}\s+(\d{{1,2}}),?\s+(\d{{4}})")
            for pattern in patterns:
                match = re.fullmatch(pattern, value)
                if match:
                    return date(int(match[2]), numero, int(match[1]))
    raise ValueError(f"data completa non riconosciuta: {value!r}")


def _mesi_prima(fine, mesi):
    """Stesso giorno `mesi` mesi prima (giorno ridotto a fine mese se serve)."""
    from calendar import monthrange
    indice = fine.year * 12 + fine.month - 1 - mesi
    anno, mese = divmod(indice, 12)
    mese += 1
    return date(anno, mese, min(fine.day, monthrange(anno, mese)[1]))


def _periodo_testuale(match, tipo, fine_libera=False):
    """Read explicit dates or a declared number of complete calendar months.

    `fine_libera` (solo con periodo_regola dichiarata nel profilo): anni fiscali a
    52 settimane chiudono il trimestre di sabato, non a fine mese.
    """
    groups = match.groupdict()
    if 'anno' in groups:
        if not re.fullmatch(r'\d{4}', groups['anno'] or ''):
            raise ValueError('anno comune non completo')
        anno = int(groups['anno'])
        numeric = {'giorno_inizio', 'mese_inizio', 'giorno_fine', 'mese_fine', 'anno'}
        if set(groups) == numeric:
            inizio = date(anno, int(groups['mese_inizio']), int(groups['giorno_inizio']))
            fine = date(anno, int(groups['mese_fine']), int(groups['giorno_fine']))
        elif set(groups) == {'inizio', 'fine', 'anno'}:
            inizio, fine = (_data(groups[k] + ' ' + groups['anno']) for k in ('inizio', 'fine'))
        else:
            raise ValueError('anno comune: estremi testuali o gruppi giorno/mese espliciti richiesti')
        if inizio > fine:
            raise ValueError('anno comune: intervallo invertito; nessun anno precedente implicito')
        return inizio, fine, {'regola': 'anno_comune_esplicito', 'anno': anno,
            'periodo_inizio': inizio.isoformat(), 'periodo_fine': fine.isoformat()}
    if {"inizio", "fine"} <= set(groups) and "mesi" not in groups:
        return _data(groups["inizio"]), _data(groups["fine"]), None
    if set(groups) != {"mesi", "fine"}:
        raise ValueError("periodo: servono gruppi inizio/fine oppure mesi/fine")
    counts = {"3": 3, "three": 3, "6": 6, "six": 6,
              "9": 9, "nine": 9, "12": 12, "twelve": 12,
              # fase C: relazioni IR italiane e tedesche («sei mesi chiusi al …»)
              "tre": 3, "sei": 6, "nove": 9, "dodici": 12,
              "drei": 3, "sechs": 6, "neun": 9, "zwölf": 12,
              # durata dichiarata dal titolo della relazione («Half-Year … as of», «semestrale al»)
              "half": 6, "semestrale": 6,
              # APERTO-TI 06/10: regola standard 6-K FPI («second quarter ended June 30, 2026»)
              "quarter": 3}
    mesi = counts.get(_norm(groups["mesi"]).lower())
    expected = {"annuale": 12, "semestrale": 6, "trimestrale": 3, "nove_mesi": 9}[tipo]
    if mesi != expected:
        raise ValueError("durata testuale in mesi assente o incompatibile con il tipo")
    fine = _data(groups["fine"])
    from calendar import monthrange
    if fine_libera and fine.day != monthrange(fine.year, fine.month)[1]:
        from datetime import timedelta
        inizio = _mesi_prima(fine, mesi) + timedelta(days=1)
        return inizio, fine, {"regola": "mesi_con_fine_libera", "mesi": mesi,
                              "periodo_inizio": inizio.isoformat(), "periodo_fine": fine.isoformat()}
    if fine.day != monthrange(fine.year, fine.month)[1]:
        raise ValueError("durata in mesi: data finale non a fine mese; servono date esplicite")
    first = fine.year * 12 + fine.month - mesi
    inizio = date(first // 12, first % 12 + 1, 1)
    return inizio, fine, {"regola": "mesi_calendario_con_fine_mese", "mesi": mesi,
                          "periodo_inizio": inizio.isoformat(), "periodo_fine": fine.isoformat()}


def _id(value):
    namespace, ident = str(value).split(":", 1)
    if namespace.upper() == "CIK":
        if not ident.isdigit():
            raise ValueError("CIK non numerico")
        ident = str(int(ident)).zfill(10)
    elif namespace.upper() == "LEI":
        ident = ident.upper()
    elif namespace.upper() != "EMITTENTE":
        raise ValueError("namespace emittente non supportato")
    if not ident.strip():
        raise ValueError("identificatore emittente vuoto")
    return f"{namespace.upper()}:{ident}"


def _campo_xml(block, campo):
    return re.search(rf"<(?:[\w.-]+:)?{campo}\b[^>]*>(.*?)</(?:[\w.-]+:)?{campo}\s*>",
                     block, re.S | re.I)


def _contesti(markup):
    """I contesti con dimensioni non sono prova del periodo dell'intero emittente."""
    for match in re.finditer(r"<(?:[\w.-]+:)?context\b[^>]*>.*?</(?:[\w.-]+:)?context\s*>",
                             markup, re.S | re.I):
        block = match[0]
        if re.search(r"<(?:[\w.-]+:)?(?:scenario|segment)\b", block, re.I):
            continue
        identifier = _campo_xml(block, "identifier")
        start, end = _campo_xml(block, "startDate"), _campo_xml(block, "endDate")
        if not identifier or not start or not end:
            continue
        scheme = re.search(r'\bscheme\s*=\s*[\"\']([^\"\']+)', identifier[0], re.I)
        scheme = scheme[1].lower() if scheme else ""
        namespace = "CIK" if "sec.gov/cik" in scheme else "LEI" if "17442" in scheme or "lei" in scheme else None
        if not namespace:
            continue
        yield {"emittente_id": _id(f"{namespace}:{unescape(identifier[1]).strip()}"),
               "inizio": _data(start[1]), "fine": _data(end[1]), "match": match}


def _prova(testo, inizio, fine, supporto, url, digest):
    return {"url": url, "sha256": digest, "supporto": supporto,
            "unita_offset": "caratteri Unicode nel markup decodificato" if supporto == "markup"
                            else "caratteri Unicode nel testo estratto",
            "inizio": inizio, "fine": fine, "testo": testo[inizio:fine]}


def _cerca_prova(testo, pattern, campo, url, digest):
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError(f"regola di verifica {campo} mancante")
    # REV_G2b A1/C1: il pattern del profilo si ESEGUE solo nel processo separato con tempo massimo;
    # qui arriva l'esito (posizioni), mai una riesecuzione nel backend. PatternTroppoLento e' un
    # ValueError: «verifica non completata: pattern troppo lento».
    match = regex_sandbox.cerca(testo, pattern)
    if not match or not match[0].strip():
        raise ValueError(f"{campo}: prova testuale assente")
    return _prova(testo, match.start(), match.end(), "testo", url, digest)


def _periodo_con_prova(testo, pattern, tipo, fine_attesa, url, digest, regola=None):
    """Shared textual-period check for original verification and PDF replay.

    `regola="piu_recente"` (dichiarata nel profilo): senza data di catalogo, il
    periodo corrente e i comparativi dell'anno prima nella stessa pagina non sono
    un'ambiguita': vince la fine piu' recente.
    """
    if not pattern:
        raise ValueError("periodo esatto non verificato: mancano contesto XBRL compatibile o date testuali")
    parsed = []
    # REV_G2b C1: corrispondenze calcolate nel processo separato (posizioni e gruppi nominati)
    for match in regex_sandbox.trova_tutti(testo, pattern):
        start, end, calculation = _periodo_testuale(match, tipo, fine_libera=regola == "piu_recente")
        parsed.append((start, end, match, calculation))
    if fine_attesa:
        if parsed and max(p[1] for p in parsed) != fine_attesa:
            raise ValueError("data catalogo diversa dal periodo testuale piu' recente: possibile comparativo")
        parsed = [p for p in parsed if p[1] == fine_attesa]
    elif regola == "piu_recente" and parsed:
        recente = max(p[1] for p in parsed)
        parsed = [p for p in parsed if p[1] == recente]
    if len({(a, b) for a, b, _, _ in parsed}) != 1:
        raise ValueError("periodo testuale assente o ambiguo")
    inizio, fine, match, calculation = parsed[0]
    proof = _prova(testo, match.start(), match.end(), "testo", url, digest)
    if calculation is not None:
        proof['calcolo'] = calculation
    return inizio, fine, proof


def _selettori(testo, regole, salta_indice=False):
    """Intestazione unica e primo confine successivo: nessuna pagina fissa.

    Inizio ripetuto anche nell'indice = ambiguita' dichiarata. Il primo confine
    completo successivo chiude la sezione; un altro nel bilancio individuale
    successivo non allunga arbitrariamente l'intervallo consolidato.
    """
    if not isinstance(regole, dict) or not regole:
        raise ValueError("regole sezioni mancanti")
    righe = [m for m in re.finditer(r"[^\n]+", testo) if m[0].strip()]
    normalizzate = [_norm(m[0]) for m in righe]
    result, motivi = {}, {}
    for nome, regola in regole.items():
        if not isinstance(regola, dict) or any(k in regola for k in ("da_pagina", "a_pagina")):
            raise ValueError(f"{nome}: pagine fisse non ammesse nell'automazione")
        try:
            a, b = regola["inizio"], regola["fine"]
            if not isinstance(a, str) or not isinstance(b, str):
                raise TypeError("regola di sezione non testuale")
            # REV_G2b C1: righe che combaciano calcolate nel processo separato; nessun re sui
            # pattern del profilo nel backend (nemmeno in ha_corpo, che usa gli stessi indici).
            idx_a, idx_b = regex_sandbox.righe_che_combaciano(normalizzate, a, b)
            idx_b = set(idx_b)
            starts = [righe[i] for i in idx_a]
            if len(starts) > 1 and salta_indice:
                # Opt-in del profilo: una voce d'indice e' seguita solo da altre voci
                # (Item/Part) o numeri di pagina fino all'intestazione finale; il corpo
                # ha almeno una riga di testo, anche breve (rimando a una nota), e poi
                # l'intestazione finale. Un'occorrenza senza fine successiva non e' la sezione.
                def ha_corpo(start):
                    testo_visto = False
                    for i, m in enumerate(righe):
                        if m.start() <= start.end():
                            continue
                        riga = normalizzate[i]
                        if i in idx_b:
                            return testo_visto
                        if not re.fullmatch(r"\d{1,4}|(?:item|part)\b.{0,200}", riga, re.I):
                            testo_visto = True
                    return False
                starts = [m for m in starts if ha_corpo(m)]
            if len(starts) != 1:
                raise ValueError("intestazione iniziale assente o ambigua")
            start = starts[0]
            ends = [righe[i] for i in sorted(idx_b) if righe[i].start() > start.end()]
            if not ends:
                raise ValueError("intestazione finale assente")
            end = ends[0]
            # Il motore pubblicato usa righe esatte e occorrenze esplicite.
            result[nome] = {"inizio": _norm(start[0]), "fine": _norm(end[0]),
                            "occorrenza_inizio": sum(_norm(m[0]) == _norm(start[0]) for m in righe
                                                     if m.start() <= start.start()),
                            "occorrenza_fine": sum(_norm(m[0]) == _norm(end[0]) for m in righe
                                                   if m.start() <= end.start())}
        except (KeyError, ValueError, TypeError, re.error) as exc:
            result[nome] = {}  # il motore marca la sezione non disponibile
            motivi[nome] = str(exc)
    return result, motivi


# Collegamenti decisi dal codice (non da un alias verificato, dall'utente o a mano): seguito revisione G1.
_COLLEGAMENTI_AUTOMATICI = ("ticker", "nome")


def _registrante(attivo):
    """dei:EntityRegistrantName del documento inline-XBRL, None se non dichiarato."""
    m = re.search(r'<(?:[\w.-]+:)?nonNumeric\b(?=[^>]*\sname\s*=\s*[\"\'](?:[\w.-]+:)?EntityRegistrantName[\"\'])'
                  r'[^>]*>(.*?)</(?:[\w.-]+:)?nonNumeric\s*>', attivo or "", re.I | re.S)
    nome = _norm(unescape(re.sub(r"<[^>]+>", "", m[1]))) if m else ""
    return nome or None


def verifica_documento(path, *, url, profilo, catalogo=None):
    """Verifica del documento con un tempo TOTALE per le prove dei pattern del profilo (REV_G2b A1:
    un profilo con molti pattern lenti non somma N volte il tempo di una prova)."""
    with regex_sandbox.budget(regex_sandbox.TEMPO_DOCUMENTO_S):
        return _verifica_documento(path, url=url, profilo=profilo, catalogo=catalogo)


def _verifica_documento(path, *, url, profilo, catalogo=None):
    """Restituisce il documento I-20 soltanto se ogni metadato ha una prova.

    HTML: identita'/periodo da contesto XBRL e data del catalogo, oppure dalle
    regole testuali esplicite come nei PDF. Date solo in titolo/filename e
    periodo riportato nel catalogo non bastano, da soli, alla verifica.
    """
    out = {"stato": "non_verificato", "motivi": []}
    ocr = None
    esterni = profilo.get("documenti_esterni") if isinstance(profilo, dict) else None
    riscontro = None
    if isinstance(esterni, dict) and url in esterni:
        # impianto 07/10 (main): un documento da fonte esterna (CDN generico, sito di gruppo, redirect fuori
        # dominio) non diventa MAI verificato, qualunque cosa dica il testo: contesto etichettato, mai numeri.
        # Eccezione (main 07/10): i BYTE riscontrati all'attivazione (sha256 salvato in «documenti_riscontrati»)
        # restano verificabili con l'etichetta salvata; se il file cambia, torna «fonte esterna».
        riscontrati = profilo.get("documenti_riscontrati")
        salvato = riscontrati.get(url) if isinstance(riscontrati, dict) else None
        salvato = salvato if isinstance(salvato, dict) and salvato.get("sha256") else None
        try:
            attuale = hashlib.sha256(Path(path).read_bytes()).hexdigest() if salvato else None
        except OSError:
            attuale = None
        if not (attuale and salvato["sha256"] == attuale):
            out["motivi"].append(f"fonte esterna ({esterni[url]}), identita' non provabile: documento mai verificato"
                                 + (" (byte diversi da quelli riscontrati all'attivazione)" if salvato else ""))
            return out
        riscontro = {"host": esterni[url], "sha256": attuale, "etichetta": salvato.get("etichetta")}
    try:
        catalogo = catalogo or {}
        meta = {k: profilo[k] for k in ("emittente_id", "lingua", "tipo", "perimetro")}
        meta["emittente_id"] = _id(meta["emittente_id"])
        if meta["tipo"] not in _DURATE:
            raise ValueError("tipo di relazione non supportato")
        if not all(isinstance(v, str) and v.strip() for v in meta.values()):
            raise ValueError("metadati del profilo incompleti")
        if catalogo.get("emittente_id") and _id(catalogo["emittente_id"]) != meta["emittente_id"]:
            raise ValueError("emittente del catalogo diverso dal profilo")
        form = catalogo.get("form", "")
        if form.endswith("/A") or catalogo.get("rettifica"):
            raise ValueError("rettifica: confronto delle versioni non ancora implementato")
        expected_types = {"10-K": {"annuale"}, "20-F": {"annuale"}, "40-F": {"annuale"},
                          "10-Q": {"trimestrale", "semestrale", "nove_mesi"}}
        if form in expected_types and meta["tipo"] not in expected_types[form]:
            raise ValueError("tipo di relazione incompatibile con il form SEC")
        if catalogo.get("language") and catalogo["language"].lower().split("-")[0] != meta["lingua"]:
            raise ValueError("lingua del catalogo diversa dal profilo")
        raw = Path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        estrazione = estrai_testo(str(path), contenuto=raw)
        if estrazione["stato"] != "ok":
            raise ValueError(estrazione.get("motivo", "documento illeggibile"))
        vuote = estrazione.get("pagine_senza_testo") or []
        if estrazione["formato"] == "pdf" and len(vuote) >= 5 and len(vuote) * 2 > (estrazione.get("pagine") or 0):
            # Solo per spiegare un fallimento: un PDF che si verifica comunque resta valido.
            ocr = f"PDF quasi senza testo ({len(vuote)} pagine su {estrazione['pagine']}): serve OCR"
        testo = estrazione["testo"]
        if len(testo) > 5_000_000:
            raise ValueError("documento oltre il limite del motore I-20")
        regole = profilo.get("verifica", {})
        prove = {k: _cerca_prova(testo, regole.get(k), k, url, digest)
                 for k in ("lingua", "tipo", "perimetro")}
        markup, contexts = "", []
        if estrazione["formato"] == "html":
            markup = raw.decode(estrazione.get("codifica", "utf-8-sig"))
            parser = _MarkupAttivo(markup)
            attivo = parser.testo_attivo()
            if any(not lang or lang.lower().split("-")[0] != meta["lingua"] for lang in parser.lingue):
                raise ValueError("lingua HTML diversa dal profilo; nessuna traduzione automatica")
            tipi_dichiarati = re.findall(
                r'<(?:[\w.-]+:)?nonNumeric\b(?=[^>]*\sname\s*=\s*[\"\'](?:[\w.-]+:)?DocumentType[\"\'])[^>]*>(.*?)</(?:[\w.-]+:)?nonNumeric\s*>',
                attivo, re.I | re.S)
            for dichiarazione in tipi_dichiarati:
                tipo_doc = _norm(unescape(re.sub(r"<[^>]+>", "", dichiarazione)))
                if tipo_doc.endswith("/A"):
                    raise ValueError("rettifica dichiarata nel documento: versione non supportata")
                if form and tipo_doc != form:
                    raise ValueError("tipo SEC dichiarato nel documento diverso dal catalogo")
            contexts = list(_contesti(attivo))
        matching = [c for c in contexts if c["emittente_id"] == meta["emittente_id"]]
        if contexts and not matching:
            raise ValueError("identificatore XBRL diverso dall'emittente atteso")
        if matching:
            m = matching[0]["match"]
            prove["emittente"] = _prova(markup, m.start(), m.end(), "markup", url, digest)
            if profilo.get("origine_collegamento") in _COLLEGAMENTI_AUTOMATICI:
                # Seguito revisione G1: il CIK del markup lega il documento al CIK del profilo, non
                # il CIK al titolo del portafoglio. Per i collegamenti automatici il registrante deve
                # avere un nome compatibile col titolo; se il documento non lo dichiara, vale la
                # prova testuale del nome come per i documenti senza XBRL.
                registrante = _registrante(attivo)
                if registrante is not None:
                    from bellomberg.market_data.filing_identita import nomi_compatibili
                    if not nomi_compatibili(profilo.get("nome"), registrante):
                        raise ValueError(f"registrante del documento ({registrante[:80]}) diverso dal titolo del "
                                         f"portafoglio ({str(profilo.get('nome'))[:80]}): collegamento da confermare")
                else:
                    prove["emittente_nome"] = _cerca_prova(testo[:20_000], regole.get("emittente"), "emittente",
                                                           url, digest)
        else:
            # Identita' testuale limitata alla parte iniziale; non il nome di un
            # concorrente citato nella lunga sezione dei rischi.
            prove["emittente"] = _cerca_prova(testo[:20_000], regole.get("emittente"), "emittente", url, digest)
        period_end = catalogo.get("report_date") or catalogo.get("period_end")
        fine_attesa = _data(period_end) if period_end else None
        low, high = _DURATE[meta["tipo"]]
        stessa_durata = [c for c in matching if low <= (c["fine"]-c["inizio"]).days+1 <= high]
        # Un contesto mensile dopo la chiusura (es. buyback successivi) non e'
        # un nuovo annuale; confrontare solo la durata del report richiesto.
        if stessa_durata and fine_attesa and max(c["fine"] for c in stessa_durata) != fine_attesa:
            raise ValueError("data catalogo diversa dal periodo XBRL omologo piu' recente: possibile comparativo o catalogo errato")
        regola = profilo.get("periodo_regola")
        if not fine_attesa and regola == "piu_recente" and stessa_durata:
            fine_attesa = max(c["fine"] for c in stessa_durata)  # comparativi XBRL dell'anno prima
        eligible = [c for c in stessa_durata if fine_attesa == c["fine"]]
        periods = {(c["inizio"], c["fine"]) for c in eligible}
        if len(periods) > 1:
            raise ValueError("periodi XBRL ambigui per la stessa data di report")
        if periods:
            inizio, fine = next(iter(periods))
            m = eligible[0]["match"]
            prove["periodo"] = _prova(markup, m.start(), m.end(), "markup", url, digest)
        else:
            inizio, fine, prove["periodo"] = _periodo_con_prova(
                testo, regole.get("periodo"), meta["tipo"], fine_attesa, url, digest, regola=regola)
        if not low <= (fine-inizio).days+1 <= high:
            raise ValueError("durata del periodo incompatibile con il tipo")
        if fine > date.today():
            raise ValueError("periodo futuro: non e' una relazione consuntiva")
        meta.update(periodo_inizio=inizio.isoformat(), periodo_fine=fine.isoformat())
        if profilo.get("sezioni_intero"):
            # Opt-in (allegati 6-K a struttura libera): una sola sezione, tutto il testo.
            non_vuote = [_norm(m[0]) for m in re.finditer(r"[^\n]+", testo) if m[0].strip()]
            if len(non_vuote) < 2:
                raise ValueError("testo intero: documento senza righe sufficienti")
            selettori = {"testo": {"inizio": non_vuote[0], "fine": non_vuote[-1], "occorrenza_inizio": 1,
                                   "occorrenza_fine": non_vuote.count(non_vuote[-1])}}
            problemi_sezioni = {}
        else:
            selettori, problemi_sezioni = _selettori(testo, profilo.get("sezioni"),
                                                     salta_indice=bool(profilo.get("sezioni_salta_indice")))
        doc = prepara_documento(path, url=url, metadati=meta, sezioni=selettori)
        if doc.get("sha256") != digest:
            raise ValueError("documento cambiato durante la verifica: hash differente")
        if doc["stato"] != "ok":
            raise ValueError(doc.get("motivo", "preparazione fallita"))
        for nome, motivo in problemi_sezioni.items():
            doc["sezioni"][nome] = {"stato": "non_disponibile", "motivo": motivo}
        doc["metadati_verifica"] = "verificati con regole del profilo e prove nei byte del documento"
        doc["prove_verifica"] = prove
        if estrazione['formato'] == 'pdf':
            doc['filing_verification'] = {
                'schema': 'filing_pdf_v1', 'url': url, 'document_sha256': digest,
                'text_sha256': hashlib.sha256(testo.encode('utf-8')).hexdigest(),
                'metadata': deepcopy(meta), 'proofs': deepcopy(prove),
                'rules': {k: regole[k] for k in ('emittente', 'lingua', 'tipo', 'perimetro', 'periodo')},
                'identity_basis': 'curated_filing_profile', 'security_identity_verified': False}
        if riscontro:
            if riscontro["sha256"] != digest:
                raise ValueError("documento cambiato dopo il riscontro dei byte: hash differente")
            doc["riscontro_esterno"] = riscontro
            return {"stato": "ok", "motivi": [], "documento": doc, "etichetta": riscontro["etichetta"],
                    "riscontro_esterno": riscontro}
        return {"stato": "ok", "motivi": [], "documento": doc}
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError, re.error) as exc:
        out["motivi"].append((f"{ocr}; " if ocr else "") + f"{type(exc).__name__}: {exc}")
        return out


# APERTO-TI (PM 06/10, «piu' aperti»): regola standard per gli allegati 6-K delle societa' estere
# quotate in USA. I profili 6-K automatici chiedevano «three months ended», ma molti emittenti
# scrivono «second quarter ended June 30, 2026» o «period ended ...»: il trimestrale restava fuori.
# Vale per OGNI profilo con variante 6-K (anche quelli gia' salvati), SOLO dopo che la verifica col
# profilo e' fallita su tipo o periodo; identita', lingua e perimetro restano quelli del profilo.
_DATA_STD = r"(?P<fine>[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})"
TIPO_6K_STANDARD = r"(?:months?|quarter|half[-\s]year|half|periods?)\s+ended|half[-\s]year"
# R-FONTI 10/10 (Opus 5.5): «three-month period ended», «three-months period ended», «three month period
# ended», «quarter ended» (prova reale: 6-K Q1 2026 di un emittente del book). Trattini tipografici ammessi (U+2010-U+2013) e
# nessuna cifra/parola attaccata davanti: «13-month» o «twenty-three months» non sono un trimestre.
_TRATTINO_STD = "\\s\\-‐‑‒–"
PERIODO_6K_STANDARD = {
    "trimestrale": (rf"(?<![\w{_TRATTINO_STD[2:]}])(?:(?:first|second|third|fourth|1st|2nd|3rd|4th)\s+)?"
                    r"(?P<mesi>three|3|quarter)"
                    rf"(?:(?:\s+and\s+(?:six|nine))?[{_TRATTINO_STD}]+months?(?:\s+periods?)?)?\s+ended(?:\s+on)?\s+"
                    + _DATA_STD),
    "semestrale": (rf"(?<![\w{_TRATTINO_STD[2:]}])(?:first\s+)?(?P<mesi>six|6|half)"
                   rf"(?:[{_TRATTINO_STD}]+(?:months?|year)(?:\s+periods?)?)?\s+ended(?:\s+on)?\s+" + _DATA_STD)}
REGOLA_6K_STANDARD = "standard_6k_fpi (regola del codice, non del profilo)"
_DATE_VISTE = re.compile(r"(?:ended(?:\s+on)?|as\s+of)\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})", re.I)
_REGISTRANTE_COPERTINA = re.compile(r"([^\n]{2,160})\n[\s\xa0]*\(Exact name of registrant", re.I)


# Revisione R-FASE M1: sotto il CIK del titolo la COPERTINA del 6-K porta sempre il registrante; il
# comunicato allegato puo' essere di un partner o di una controllata. L'identita' si prova nel CORPO.
_FINE_COPERTINA_6K = re.compile(r"\(Exact name of registrant[^\n]*\)|Indicate by check mark[^\n]*|Form\s+40-F[^\n]*"
                                r"|Commission File Number[^\n]*", re.I)


def _corpo_6k(testo):
    """Testo dopo il blocco di copertina del 6-K (registrante, «Indicate by check mark», Form 20-F/40-F);
    tutto il testo se la copertina non c'e' (allegato EX-99 a se')."""
    fine = 0
    for m in _FINE_COPERTINA_6K.finditer(testo[:8000]):
        fine = m.end()
    return testo[fine:]


_PERIODO_FRASE = re.compile(r"\b(?:quarter|months?|half[-\s]?year|six[-\s]month|period|semester)s?\s+ended\b", re.I)
_ABBREVIAZIONI = {"ltd", "inc", "corp", "co", "no", "s.a", "n.v", "plc", "mr", "ms", "dr", "st", "s.p.a", "a.g", "n.a",
                  # v2 (prova reale: comunicato 6-K di un emittente del book, «reported U.S. GAAP financial results for
                  # the second quarter ended ...» spezzato dopo «U.S.» e «GAAP» preso per un'altra entita')
                  "u.s", "u.k", "u.s.a", "e.g", "i.e", "vs", "approx"}
_DATELINE = re.compile(r"^\s*[A-ZÀ-Þ][A-ZÀ-Þa-zà-ÿ .,'’-]{0,60}?,?\s+(?:[A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4}"
                       r"|\d{1,2}\s+[A-Z][a-z]+\s+\d{4})\s*(?:\([^)]{0,40}\)\s*)?[-–—/]+\s*")


_TESTA_CORPO_6K = 6000  # battute del corpo in cui contano le frasi sul periodo (oltre: note al bilancio)


def _fine_frase(dopo):
    """Fine della frase in «dopo» (dal periodo in poi): punto/!/? seguito da maiuscola o fine riga, saltando le
    abbreviazioni («Corp.», «Ltd.») e le date («June 30, 2025.» chiude)."""
    for b in re.finditer(r"[.!?](?=\s+[A-ZÀ-Þ“\"(]|\s*$)", dopo):
        parola = re.findall(r"[\w.]+$", dopo[:b.start()])
        if b.group(0) == "." and parola and parola[0].lower().rstrip(".") in _ABBREVIAZIONI:
            continue
        return b.end()
    return len(dopo)


def _frasi_del_periodo(corpo, limite=_TESTA_CORPO_6K):
    """Frasi INTERE che contengono il periodo («… quarter ended …», anche la parte dopo la data), senza
    dateline e «(Business Wire)», nelle prime ``limite`` battute del corpo (la testa del comunicato)."""
    out, fine_prec = [], 0
    for m in _PERIODO_FRASE.finditer(corpo[:limite]):
        if m.start() < fine_prec:
            continue  # stessa frase di un periodo gia' letto
        riga = corpo[max(0, m.start() - 600):m.start()].split("\n")[-1]
        inizio = 0
        for b in re.finditer(r"\.\s+(?=[A-Z])", riga):
            parola = re.findall(r"[\w.]+$", riga[:b.start()])
            if parola and parola[0].lower().rstrip(".") in _ABBREVIAZIONI:
                continue
            inizio = b.end()
        dopo = corpo[m.start():m.start() + 600].split("\n")[0]
        fine = _fine_frase(dopo)
        frase = _DATELINE.sub("", riga[inizio:]) + dopo[:fine]
        out.append(re.sub(r"^\s*(?:\([^)]{0,40}\)\s*[-–—]*\s*)?(?:the\s+)?", "", frase, flags=re.I))
        fine_prec = m.start() + fine
    return out


def _frase_del_periodo(corpo):
    """La PRIMA frase sul periodo (vedi ``_frasi_del_periodo``); None se il corpo non ne ha."""
    frasi = _frasi_del_periodo(corpo, len(corpo))
    return frasi[0] if frasi else None


# Parole che possono aprire una frase sul periodo senza essere un'entita' («In the second quarter ended …»,
# «As of and for the three months ended …», titoli dei prospetti): ogni altra parola maiuscola e' un nome proprio
_PAROLE_COMUNI_6K = frozenset((
    "in as of and for the during on at our its this these a an q1 q2 q3 q4 h1 h2 fy ytd first second third fourth "
    "quarter quarterly half year years month months three six nine twelve fiscal results result financial statements "
    "statement consolidated condensed interim unaudited audited report reports announces announced period periods "
    "ended highlights revenue revenues net income total today released operating key figures earnings "
    "january february march april may june july august september october november december "
    # impianto 6K 07/10: pronomi, nomi generici del registrante, parole dei titoli dei prospetti
    "we it company group groups bank management board directors shareholders stockholders operations profit loss "
    "losses cash flows flow changes equity comprehensive position notes selected unaudited summary "
    # v2: principi contabili e etichette d'allegato (prova reale: «Enclosure: <Registrante>'s Second Quarter ...»,
    # «non-U.S. GAAP»), mai nomi di un'altra entita'
    "gaap ifrs non u s enclosure enclosures exhibit exhibits attachment attachments annex "
    # v2: parole funzionali che aprono una frase («There were no material changes in the first six months ...»,
    # prova reale sullo stesso 6-K): lista CHIUSA di determinanti, pronomi e avverbi, mai ragioni sociali
    "there this that these those such each all any some other others both either neither no not one following "
    "based according due given including excluding while when where if although since because however further "
    "furthermore moreover accordingly therefore thus then also overall below above see refer please note "
    "certain several many most more less").split())
# Il registrante senza nome: «we», «the Company», «the Group»; i possessivi aprono un sintagma da leggere
_PRONOME_6K = re.compile(r"(?:we|it|management|(?:the\s+)?(?:company|group|bank)(?!['’]))\b|(?P<poss>our|its|"
                         r"(?:the\s+)?(?:company|group|bank)['’]s?)(?=\s)", re.I)
# Apertura avverbiale o sul periodo chiusa da una virgola («In the second quarter ended June 30, 2025, …»,
# «Today, …»): la virgola dentro una data («June 30, 2025») non la chiude
_APERTURA_6K = re.compile(r"(?:(?:in|for|during|as\s+(?:of|at)|over|through(?:out)?|at|on|since|compared\s+(?:to|with)"
                          r"|following|after|today|yesterday|earlier\s+today|separately|additionally|also|meanwhile"
                          r"|recently|in\s+addition)\b(?:[^,]|,\s*\d{4}\b){0,160}?,(?!\s*\d{4}\b)\s*)+", re.I)
_FORME_DOPO_6K = re.compile(r"(?:[\s,]*(?:ltd|limited|inc|incorporated|corp|corporation|plc|s\.?a|n\.?v|ag|se|"
                            r"s\.?p\.?a|llc|co|group|holdings?)\b\.?)*(?:\s*\([^)]{0,60}\))?", re.I)
# Parole che chiudono il sintagma del soggetto (preposizioni e verbi tipici del comunicato)
_FINE_SINTAGMA_6K = frozenset((
    "in for at during from on to by with today was were is are has have had rose grew increased decreased "
    "reached totaled totalled released reports reported announced announces said recorded delivered posted").split())
_PASSIVO_6K = re.compile(r"\b(?:released|reported|announced|published|issued|presented|disclosed|furnished)\s+"
                         r"(?:today\s+)?by\s+(?:the\s+)?", re.I)
_MAIUSCOLA_6K = r"[A-ZÀ-Þ][\w&'’.-]*"


# v2: etichetta dell'allegato in testa alla frase («Enclosure:», «Exhibit 99.1 -», «Attachment:»): non e' il
# soggetto della frase
_ETICHETTA_ALLEGATO_6K = re.compile(r"^(?:enclosures?|exhibits?(?:\s+no\.?)?(?:\s+\d+(?:\.\d+)*)?|attachments?"
                                    r"|annex(?:es)?)\s*[:\-–—]\s*", re.I)


def _comune_6k(parola):
    parti = [p for p in re.split(r"[-'’.]", parola.lower()) if p]
    return not parti or all(p in _PAROLE_COMUNI_6K or p.isdigit() for p in parti)


def _alias_in_testa(testo, rxs):
    """Fine del PIU' LUNGO alias del registrante all'inizio di ``testo`` (None se non comincia con un alias)."""
    fini = [m.end() for m in (re.match(rx, testo, re.I) for rx in rxs) if m]
    return max(fini) if fini else None


def _nome_proprio(testo, rxs):
    """Il nome proprio (parole maiuscole consecutive) con cui comincia ``testo`` se NON e' il registrante ne'
    una parola comune, altrimenti None."""
    t = re.match(_MAIUSCOLA_6K, testo)
    if not t or _comune_6k(t[0]) or _alias_in_testa(testo, rxs) is not None:
        return None
    return re.match(rf"{_MAIUSCOLA_6K}(?:\s+{_MAIUSCOLA_6K})*", testo)[0].rstrip(".,;:")


def _nome_nel_sintagma(resto, rxs, solo_specificazione=False):
    """Altra entita' nominata nel sintagma del soggetto (fino a preposizione/verbo, max 8 parole): «payments
    partner Zzpartner» -> Zzpartner. Con ``solo_specificazione`` conta solo «… of <Nome>»."""
    for i, w in enumerate(re.finditer(r"\S+", resto)):
        parola = w[0].strip("\"“”,;:")
        if i >= 8:
            break
        if parola.lower() == "of":
            nome = re.match(r"\s*of\s+(?:the\s+)?", resto[w.start():], re.I)
            return _nome_proprio(resto[w.start() + nome.end():], rxs) if nome else None
        if parola.lower() in _FINE_SINTAGMA_6K:
            break
        if not solo_specificazione:
            nome = _nome_proprio(resto[w.start():], rxs)
            if nome:
                return nome
    return None


# Citazioni/attribuzioni (main 07/10): «John Smith, CEO, said …» ha per soggetto una PERSONA, non un'entita'
# societaria. Persona = 2-3 parole maiuscole senza parole societarie, in una frase con verbo di dichiarazione e
# (carica nella frase o verbo subito dopo il nome). Un nome di una parola («Zzpartner») resta un'entita'.
_DICHIARA_6K = re.compile(r"\b(?:said|says|commented|comments|stated|states|added|adds|noted|notes|explained|"
                          r"ha\s+dichiarato|ha\s+commentato|erkl(?:ä|ae)rte|sagte|a\s+d(?:é|e)clar(?:é|e))\b", re.I)
_CARICA_6K = re.compile(r"\b(?:CEO|CFO|COO|chief\s+\w+\s+officer|president|chairman|chairwoman|chair|director|"
                        r"head\s+of|founder|amministratore\s+delegato|vorstand\w*|directeur)\b", re.I)
_PAROLE_SOCIETARIE_6K = frozenset((
    "ltd limited inc incorporated corp corporation plc sa nv ag se spa llc co company group holding holdings bank "
    "capital finance partner partners payments services international trust fund gmbh bv ab asa oyj").split())


def _persona_che_dichiara(frase, nome):
    parole = str(nome).replace(",", " ").split()
    if not 2 <= len(parole) <= 3 or any(p.lower().strip(".") in _PAROLE_SOCIETARIE_6K for p in parole):
        return False
    if not _DICHIARA_6K.search(frase):
        return False
    dopo = frase[frase.find(nome) + len(nome):] if nome in frase else ""
    return bool(_CARICA_6K.search(frase) or _DICHIARA_6K.match(dopo.lstrip(" ,")))


def _altro_soggetto(frase, alias):
    """Nome dell'altra entita' SOGGETTO della frase sul periodo; None se il soggetto e' il registrante (un suo
    alias, «we», «the Company», «the Group») o se la frase non nomina un'altra entita' come soggetto: allora
    nel 6-K del registrante vale per il registrante (impianto 07/10, R-SITI2 F1). Altro soggetto (R-SITI2 E8):
    - nome proprio in testa che non e' un alias («Zzpartner today released …», «Zztest Pagamentos, a subsidiary
      of …»), anche dopo un'apertura con virgola («In the second quarter ended June 30, 2025, Zzpartner …»);
    - alias esteso da un altro nome («Zztest Payments» col marchio «Zztest»), soggetto composto
      («Zztest Holdings and Zzpartner …»), possessivo («Zztest Holdings' payments partner Zzpartner …»,
      «Our payments partner Zzpartner …»), specificazione («The financial statements of Zzpartner …»);
    - passivo con un altro agente («… were released today by Zzpartner»)."""
    from bellomberg.market_data.filing_attivazione import regex_alias
    rxs = [rx for rx in (regex_alias(a["nome"]) for a in alias) if rx]
    testo = re.sub(r"^[\s\"“‘'(]*(?:the\s+)?", "", frase, flags=re.I)
    testo = re.sub(_ETICHETTA_ALLEGATO_6K, "", testo, count=1)  # «Enclosure: ...», «Exhibit 99.1 - ...»
    testo = re.sub(r"^[\s\"“‘'(]*(?:the\s+)?", "", _APERTURA_6K.sub("", testo, count=1), flags=re.I)
    altro = None
    fine = _alias_in_testa(testo, rxs)
    pronome = _PRONOME_6K.match(testo) if fine is None else None
    if fine is not None:
        dopo = testo[fine:]
        dopo = dopo[_FORME_DOPO_6K.match(dopo).end():]
        if re.match(r"\s*(?:'s|’s|'|’)(?=\s)", dopo):
            altro = _nome_nel_sintagma(re.sub(r"^\s*(?:'s|’s|'|’)", "", dopo), rxs)
        elif m := re.match(r"\s*,?\s*(?:and|&|together\s+with|jointly\s+with)\s+(?:the\s+)?", dopo, re.I):
            altro = _nome_proprio(dopo[m.end():], rxs)
        elif m := re.match(r"\s+(?=[A-ZÀ-Þ])", dopo):
            nome = _nome_proprio(dopo[m.end():], rxs)
            altro = " ".join((testo[:fine] + " " + nome).split()) if nome else None
    elif pronome:
        if pronome.group("poss"):
            altro = _nome_nel_sintagma(testo[pronome.end():], rxs)
    else:
        altro = _nome_proprio(testo, rxs) or _nome_nel_sintagma(testo, rxs, solo_specificazione=True)
    if altro and not _persona_che_dichiara(frase, altro):
        return altro
    passivo = _PASSIVO_6K.search(frase)
    if passivo and not _PRONOME_6K.match(frase[passivo.end():]):
        nome = _nome_proprio(frase[passivo.end():], rxs)
        if nome:
            return nome
    return _soggetto_subordinato(frase, rxs)


# R-FONTI 10/10 v3 (Opus 5.5, riserve ALTO R2 e MEDIO R5 della revisione v2): con l'emittente soggetto della
# frase, i risultati possono essere di un'ALTRA entita' in una subordinata («Zztest Holdings announces that
# Zzbank S.A. released ... results for the three months ended ...») o in una relazione nominata («..., its
# partner, ...», «... ended June 30, 2025 of its payments partner Zzpartner S.A.»). Precisione prima del
# richiamo: basta la subordinata con un'altra entita' e un verbo di pubblicazione, oppure una parola di
# relazione insieme a un nome proprio che non e' l'emittente; il falso rifiuto e' dichiarato col nome.
_RELAZIONE_6K = re.compile(
    r"\b(?:partners?|subsidiary|affiliates?|affiliated|joint[\s-]+ventures?|investees?"
    r"|parent(?:\s+(?:company|entity))?\s+of|carve[\s-]?outs?|on\s+behalf\s+of"
    r"|whose\s+(?:[\w-]+\s+){0,4}?(?:statements?|results|accounts)\s+(?:are|is|were)\s+(?:presented|included|attached))\b",
    re.I)
_VERBO_RISULTATI_6K = re.compile(r"\b(?:released?|releases|reported|reports?|announced|announces?|published"
                                 r"|publish(?:es)?|presented|presents?|issued|issues?|posted|posts?|delivered"
                                 r"|delivers?|recorded|records?|filed|files?)\b", re.I)


def _spazi_alias(testo, rxs):
    """Intervalli di ``testo`` occupati da un alias dell'emittente (con la forma giuridica che lo segue)."""
    out = []
    for rx in rxs:
        for m in re.finditer(rx, testo, re.I):
            out.append((m.start(), m.end() + _FORME_DOPO_6K.match(testo, m.end()).end() - m.end()))
    return out


def _soggetto_subordinato(frase, rxs):
    """Altra entita' a cui la frase attribuisce i risultati fuori dal soggetto principale; None altrimenti."""
    for m in re.finditer(r"\bthat\s+(?:the\s+)?", frase, re.I):
        resto = frase[m.end():]
        pronome = _PRONOME_6K.match(resto)
        if pronome:
            nome = _nome_nel_sintagma(resto[pronome.end():], rxs) if pronome.group("poss") else None
        else:
            nome = _nome_proprio(resto, rxs)
        if nome and _VERBO_RISULTATI_6K.search(resto[:240]) and not _persona_che_dichiara(frase, nome):
            return nome
    relazione = _RELAZIONE_6K.search(frase)
    if relazione:
        alias = _spazi_alias(frase, rxs)
        for w in re.finditer(r"(?<![\w&'’.-])[A-ZÀ-Þ]", frase):
            if any(a <= w.start() < b for a, b in alias):
                continue
            nome = _nome_proprio(frase[w.start():], rxs)
            if nome and not _persona_che_dichiara(frase, nome):
                return f"{nome}, relazione «{' '.join(relazione[0].split())}»"
    return None


# R-FONTI 10/10 v2 (Opus 5.5, riserva ALTO-1 del revisore): il 6-K legittimo che si apre con l'indice e la
# relazione del revisore (prova reale: 6-K Q1 2026 di un emittente del book, nome dell'emittente nel corpo solo
# dopo ~25.000 battute) non ha l'alias nella testa del corpo. Si riconosce dai SEGNALI STRUTTURALI del prospetto,
# cercati nel corpo INTERO e ancorati a inizio riga (cosi' «partner of X», «subsidiary of X», «X's partner» non
# valgono: l'alias deve stare da solo sulla riga, seguito al piu' dalla forma giuridica).
# v3 (Opus 5.5, riserve ALTO R3/R9 e MEDIO R4/R10 della revisione v2): PRECISIONE prima del richiamo.
#   T = riga «<alias> <forma>» + a capo + titolo del prospetto («Unaudited Interim Condensed Consolidated
#       Financial Statements») + frase del periodo, con il BLOCCO di intestazione sopra la riga (fino a 5 righe,
#       chiuso dal numero di pagina o da una riga di testo) e la riga del titolo/periodo senza altri nomi ne'
#       relazioni («Zztest Pagamentos S.A.» / «A subsidiary of» / «Zztest Holdings Ltd.»: prospetto della
#       controllata, non dell'emittente);
#   N = nota «1. OPERATIONS» la cui «Company» e' l'emittente e la cui PRIMA FRASE non lo mette in relazione
#       con un'altra entita' («is the parent of ..., whose statements are presented herein»: no).
# Riconosciuto solo con T E N. Il documento primario del deposito non promuove piu' un segnale solo (v2: «T o N
# + primario» verificava il prospetto di una controllata intestato con l'emittente come controllante).
# Prospetti o nota 1 di un'ALTRA entita' (anche senza forma giuridica: riga di soli nomi propri sopra il titolo)
# = documento di altra entita': non verificato, dichiarato, anche quando la testa del corpo nomina l'emittente.
_FORME_RIGA_6K = (r"(?:[ \t\xa0,]*(?:ltd|limited|inc|incorporated|corp|corporation|plc|s\.?[ \t]?a|n\.?[ \t]?v|ag|se"
                  r"|s\.?[ \t]?p\.?[ \t]?a|llc|co|ltda|gmbh|b\.?[ \t]?v)\b\.?)*")
_TITOLO_PROSPETTO_6K = (r"(?:unaudited\s+)?(?:interim\s+)?(?:condensed\s+)?(?:consolidated\s+)?"
                        r"financial\s+statements")
_PERIODO_PROSPETTO_6K = r"(?:for|as\s+(?:of|at)(?:\s+and\s+for)?)\s+the\s+[^\n]{0,60}?\bended\b"
_NOTA_OPERAZIONI_6K = (r"(?:^|\n)[ \t\xa0]*1\s*[.)\-–]?[ \t\xa0]*(?:operations|general\s+information"
                       r"|corporate\s+information|reporting\s+entity|the\s+company)\b[^\n]{0,40}\n\s*")
_SOCIETA_NOTA_6K = re.compile(r"([^(]{2,120}?)\s*\(\s*[\"“”'‘’]?(?:the\s+)?(?:company|group|parent)\b", re.I)
# parole ammesse nel blocco di intestazione oltre all'emittente (titoli, indice, pagine): mai nomi di entita'
_PAROLE_INTESTAZIONE_6K = frozenset("contents page pages index table tables notes note to".split())
_CONNETTIVI_NOME_6K = frozenset("of de di do da del della dos das the and & y e et und von van".split())
_RIGA_TESTO_6K = 100  # oltre: riga di testo, non di intestazione


def _riga_dell_emittente(riga, rxs):
    return any(re.fullmatch(rf"(?:{rx}){_FORME_RIGA_6K}", riga, re.I) for rx in rxs)


def _blocco_intestazione(corpo, inizio, righe=5):
    """Righe di intestazione SOPRA la posizione ``inizio`` (dalla piu' vicina), al massimo ``righe``: il blocco
    si chiude a una riga con cifre (numero di pagina, riga di tabella, data), a una riga di testo lunga o a una
    riga chiusa da punteggiatura di frase che non sia una forma giuridica («Ltd.» resta nel blocco)."""
    from bellomberg.market_data.filing_attivazione import _FORME_ENTITA
    out = []
    for r in reversed(corpo[max(0, inizio - 1500):inizio].split("\n")):
        r = " ".join(r.split())
        if not r:
            continue
        if len(out) >= righe or not re.search(r"[^\W\d_]", r) or re.search(r"\d", r) or len(r) > _RIGA_TESTO_6K:
            break
        if re.search(r"[.:;!?]$", r) and not re.search(rf"(?<![\w]){_FORME_ENTITA}$", r):
            break
        out.append(r)
    return out


def _riga_estranea(riga, rxs):
    """True se la riga del blocco di intestazione non e' dell'emittente ne' fatta di sole parole da documento:
    un'altra ragione sociale, un nome proprio o una relazione («A subsidiary of», «Zztest Pagamentos S.A.»)."""
    from bellomberg.market_data.filing_attivazione import _PAROLE_DOCUMENTO
    if _RELAZIONE_6K.search(riga):
        return True
    if _riga_dell_emittente(riga, rxs):
        return False
    parole = re.findall(r"[^\W\d_]+", riga)
    return not all(p.lower() in _PAROLE_COMUNI_6K or p.lower() in _PAROLE_DOCUMENTO
                   or p.lower() in _PAROLE_INTESTAZIONE_6K for p in parole)


def _riga_di_nome(riga, rxs):
    """True se la riga sopra un titolo di prospetto e' la ragione sociale di un'altra entita': forma giuridica
    in fondo, relazione, oppure soli nomi propri (parole maiuscole e connettivi) non tutti parole comuni."""
    from bellomberg.market_data.filing_attivazione import _FORME_ENTITA
    if _riga_dell_emittente(riga, rxs) or len(riga) > 80:
        return False
    if re.search(rf"(?<![\w]){_FORME_ENTITA}$", riga) or _RELAZIONE_6K.search(riga):
        return True
    parole = riga.replace(",", " ").split()
    return (all(p[:1].isupper() or not p[:1].isalpha() or p.lower() in _CONNETTIVI_NOME_6K for p in parole)
            and not all(_comune_6k(p) or p.lower() in _CONNETTIVI_NOME_6K for p in parole if p[:1].isalpha()))


def _segnali_prospetto(corpo, rxs):
    """{"T": bool, "N": bool, "altre": [motivi]}: segnali strutturali del prospetto nel corpo INTERO; «altre»
    elenca prospetti e note intestati a un'altra entita' (o all'emittente in relazione con un'altra)."""
    from bellomberg.market_data.filing_attivazione import _FORME_ENTITA
    out = {"T": False, "N": False, "altre": []}

    def altra(motivo):
        if motivo[:120] not in out["altre"]:
            out["altre"].append(motivo[:120])
    for m in re.finditer(rf"(?:^|\n)[ \t\xa0]*([^\n]{{2,120}}?)[ \t\xa0]*\n\s*({_TITOLO_PROSPETTO_6K}\s*"
                         rf"{_PERIODO_PROSPETTO_6K}[^\n]*)", corpo, re.I):
        riga, titolo = " ".join(m[1].split()), " ".join(m[2].split())
        if _riga_dell_emittente(riga, rxs):
            estranee = [r for r in _blocco_intestazione(corpo, m.start(1)) if _riga_estranea(r, rxs)]
            relazione = _RELAZIONE_6K.search(titolo)
            if estranee:
                altra(f"prospetto intestato a «{' / '.join(reversed(estranee))} / {riga}»")
            elif relazione:
                altra(f"prospetto con relazione «{' '.join(relazione[0].split())}»: «{titolo}»")
            else:
                out["T"] = True
        elif _riga_di_nome(riga, rxs):
            altra(f"prospetto intestato a «{riga}»")
    for m in re.finditer(_NOTA_OPERAZIONI_6K, corpo, re.I):
        resto = corpo[m.end():m.end() + 1500]
        societa = _SOCIETA_NOTA_6K.match(resto)
        if not societa:
            continue
        nome = " ".join(societa[1].split())
        frase = " ".join(resto[:_fine_frase(resto)].split())
        if not _riga_dell_emittente(nome, rxs):
            if re.search(rf"(?<![\w]){_FORME_ENTITA}$", nome) or _riga_di_nome(nome, rxs):
                altra(f"nota «1. Operations» di «{nome}»")
        elif relazione := _RELAZIONE_6K.search(frase):
            altra(f"nota «1. Operations» con l'emittente in relazione («{' '.join(relazione[0].split())}»)")
        else:
            out["N"] = True
    return out


def identita_6k(path, *, url=None, profilo, catalogo=None, registrante=None):
    """(motivo, prova). motivo None se nel CORPO del 6-K l'emittente e' provato (soggetto nella testa del corpo,
    o segnali strutturali del prospetto T e N), nessun prospetto o nota 1 e' di un'altra entita' e nessuna frase
    sul periodo nella testa ha un altro soggetto. ``url`` e ``catalogo`` restano per compatibilita' (v3: il
    documento primario del deposito non e' piu' una prova d'identita')."""
    from bellomberg.market_data.filing_attivazione import _soggetto_in_copertina, regex_alias
    try:
        testo = estrai_testo(str(path), contenuto=Path(path).read_bytes()).get("testo", "")
    except OSError as exc:
        return f"corpo del 6-K non leggibile ({type(exc).__name__})", None
    alias = [{"nome": n, "fonte": "profilo"} for n in (profilo.get("nome"), registrante) if n]
    if registrante:  # identita' dal CIK: vale anche il marchio (prima parola del registrante, 4+ lettere)
        marchio = re.sub(r"[^\w]", "", str(registrante).split()[0]) if str(registrante).split() else ""
        if len(marchio) >= 4:
            alias.append({"nome": marchio, "fonte": "marchio del registrante"})
    if not alias:
        # profilo senza nome (scritto a mano): il nome e' il testo che la regola «emittente» del profilo riconosce
        # nel documento (eseguita nel processo separato del regex_sandbox, come in verifica_documento)
        regola = (profilo.get("verifica") or {}).get("emittente")
        try:
            m = regex_sandbox.cerca(testo, regola) if isinstance(regola, str) and regola.strip() else None
        except ValueError:
            m = None
        if m and m[0].strip():
            alias.append({"nome": " ".join(m[0].split()), "fonte": "regola emittente del profilo"})
    if not alias:
        return "corpo del 6-K: nome dell'emittente non noto", None
    corpo = _corpo_6k(testo)
    segnali = _segnali_prospetto(corpo, [rx for rx in (regex_alias(a["nome"]) for a in alias) if rx])
    if segnali["altre"]:
        return ("identita' nel corpo del 6-K non provata: prospetti o nota «1. Operations» di un'altra entita' ("
                + "; ".join(segnali["altre"][:2]) + "): documento di altra entita'"), None
    perche = _soggetto_in_copertina(corpo, alias)
    prova = "emittente soggetto nella testa del corpo del 6-K"
    if perche and perche.startswith("soggetto non provato in copertina: nessun alias"):
        nomi = [n for n, s in (("titolo del prospetto intestato all'emittente", segnali["T"]),
                               ("nota «1. Operations» dell'emittente", segnali["N"])) if s]
        if segnali["T"] and segnali["N"]:
            perche = None
            prova = "segnali strutturali del prospetto: " + " e ".join(nomi)
        else:
            perche += ("; segnali strutturali del prospetto: " + (" e ".join(nomi) or "nessuno")
                       + " (servono il titolo del prospetto intestato all'emittente E la sua nota «1. Operations»;"
                       " il documento primario del deposito non basta)")
    if perche:
        return f"identita' nel corpo del 6-K non provata: {perche}", None
    # revisione R-SITI2 D8: l'emittente deve essere il SOGGETTO della frase sul periodo, non solo citato
    # («Zzpartner, the payments partner of Zztest Holdings, today released … quarter ended …»: no)
    # impianto 6K 07/10: OGNI frase sul periodo nella testa del corpo; regola prudente, basta UNA frase con
    # un'altra entita' soggetto (comunicato congiunto o del partner dopo quello del registrante): non verificato
    altro = next((a for a in (_altro_soggetto(f, alias) for f in _frasi_del_periodo(corpo)) if a), None)
    if altro:
        return ("identita' nel corpo del 6-K non provata: la frase del periodo ha un altro soggetto "
                f"(«{' '.join(str(altro).split())[:80]}»)"), None
    return None, prova


def _identita_nel_corpo(path, profilo, registrante=None, *, url=None, catalogo=None):
    """None se nel CORPO del 6-K l'emittente e' provato (vedi ``identita_6k``), altrimenti il motivo."""
    return identita_6k(path, url=url, profilo=profilo, catalogo=catalogo, registrante=registrante)[0]


def _cik_del_percorso(url):
    m = re.search(r"/Archives/edgar/data/(\d{1,10})/", str(url or ""))
    return str(int(m[1])).zfill(10) if m else None


def verifica_6k_standard(path, *, url, profilo, catalogo=None):
    """Seconda verifica di un allegato 6-K con la regola standard (dichiarata nel risultato).

    Esiti: «ok» (periodo certo, regola dichiarata in ``regola_verifica``); «periodo_da_confermare»
    (identita', lingua, perimetro e natura di relazione provati, periodo non certo: il documento
    resta leggibile e si dichiara); altrimenti «non_verificato» col motivo. Identita' MAI rilassata:
    vale la prova del profilo; se il nome del profilo non compare, vale solo il CIK del percorso
    EDGAR uguale a quello del profilo E il registrante di copertina compatibile col nome del titolo.
    """
    tipo = profilo.get("tipo")
    if tipo not in PERIODO_6K_STANDARD:
        return {"stato": "non_verificato", "motivi": [f"regola standard 6-K non applicabile al tipo {tipo}"]}
    regole = {**(profilo.get("verifica") or {}), "tipo": TIPO_6K_STANDARD, "periodo": PERIODO_6K_STANDARD[tipo]}
    # Periodo dal testo dell'allegato: la data di catalogo di un 6-K e' spesso il mese del deposito.
    cat = {k: v for k, v in (catalogo or {}).items() if k not in ("report_date", "period_end")}
    prova = {**profilo, "verifica": regole, "periodo_regola": "piu_recente"}
    esito = verifica_documento(path, url=url, profilo=prova, catalogo=cat)
    identita, registrante = None, None
    if esito.get("stato") != "ok" and any("emittente: prova testuale assente" in m for m in esito.get("motivi", [])):
        cik = profilo.get("cik") or str(profilo.get("emittente_id", "")).removeprefix("CIK:")
        try:
            testo = estrai_testo(str(path), contenuto=Path(path).read_bytes()).get("testo", "")
        except OSError:
            testo = ""
        copertina = _REGISTRANTE_COPERTINA.search(testo[:20_000])
        registrante = _norm(copertina[1]).strip() if copertina else None
        from bellomberg.market_data.filing_identita import nomi_compatibili
        if (cik and cik.isdigit() and _cik_del_percorso(url) == str(int(cik)).zfill(10) and registrante
                and profilo.get("nome") and nomi_compatibili(profilo["nome"], registrante)):
            identita = "CIK del filing EDGAR uguale al profilo e registrante di copertina «%s»" % registrante[:80]
            esito = verifica_documento(path, url=url, profilo={**prova, "verifica": {
                **regole, "emittente": re.escape(registrante)}}, catalogo=cat)
        else:
            return {"stato": "non_verificato", "motivi": esito.get("motivi", []) + [
                "identita' non provata: nome del titolo assente e CIK/registrante di copertina non concordanti"]}
    if esito.get("stato") == "ok" or esito.get("motivi") and all(
            ("periodo" in m or "durata" in m) and "futuro" not in m for m in esito.get("motivi", [])):
        # R-FASE M1: la regola standard e' piu' larga su tipo e periodo, quindi l'identita' va provata nel
        # CORPO (fuori dalla copertina del registrante), non dove il nome sta comunque
        corpo, prova_corpo = identita_6k(path, url=url, profilo=profilo, catalogo=catalogo,
                                         registrante=registrante if identita else None)
        if corpo:
            return {"stato": "non_verificato", "motivi": list(esito.get("motivi", [])) + [corpo]}
        identita = "; ".join(x for x in (identita, prova_corpo) if x)
    if esito.get("stato") == "ok":
        return {**esito, "regola_verifica": REGOLA_6K_STANDARD,
                **({"identita_verifica": identita} if identita else {})}
    motivi = list(esito.get("motivi", []))
    if motivi and all(("periodo" in m or "durata" in m) and "futuro" not in m for m in motivi):
        # Natura di relazione, identita', lingua e perimetro superati; manca solo un periodo certo.
        try:
            testo = estrai_testo(str(path), contenuto=Path(path).read_bytes()).get("testo", "")
        except OSError:
            testo = ""
        viste = []
        for m in _DATE_VISTE.finditer(testo):
            try:
                giorno = _data(m[1]).isoformat()
            except ValueError:
                continue
            if giorno not in viste:
                viste.append(giorno)
        return {"stato": "periodo_da_confermare", "periodo_stato": "da_confermare",
                "periodi_visti": sorted(viste, reverse=True)[:10], "regola_verifica": REGOLA_6K_STANDARD,
                "motivi": ["periodo da confermare: " + "; ".join(motivi)[:300]]}
    return {"stato": "non_verificato", "motivi": motivi}
